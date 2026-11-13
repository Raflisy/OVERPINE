"""Bounded SQLite history for real sensor samples."""
from __future__ import annotations

import logging
import math
import queue
import sqlite3
import threading
import time
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.core.config import DEFAULT_HISTORY_SENSOR_IDS

_LOG = logging.getLogger("overpine.history")
_RAW_RETENTION_SECONDS = 120
_MINUTE_RETENTION_SECONDS = 600
_MINUTE_INTERVAL_SECONDS = 60
_AGGREGATE_INTERVAL_SECONDS = 300
_HEARTBEAT_SECONDS = 10.0
_VALUE_CHANGE_THRESHOLD = 0.01
_MEBIBYTE = 1024 * 1024
_MAX_PENDING_SAMPLES = 100_000
_PRUNE_BATCH_SIZE = 1000
_SCHEMA_VERSION = 2
_VALID_RESOLUTIONS = frozenset({"auto", "raw", "minute", "5-minute"})
_DETAIL_SENSOR_PREFIXES = (
    "battery.device.",
    "cpu.core.",
    "cpu.frequency-percent.",
    "gpu.engine.",
    "gpu.memory.",
    "power.power.",
    "thermal.passive-limit.",
    "thermal.temperature.",
    "thermal.throttle-reasons.",
)


class HistoryStorageError(RuntimeError):
    """Report a history database that cannot currently serve the request."""


@dataclass(frozen=True, slots=True)
class HistorySample:
    """One real value or explicit non-available state from the registry."""

    timestamp: float
    sensor_id: str
    value: float | None
    state: str


class HistoryStore:
    """Batch sensor samples in a capped, age-pruned SQLite database."""

    def __init__(
        self,
        database_path: Path,
        *,
        enabled: bool = True,
        retention_days: int = 7,
        max_mb: int = 100,
        flush_interval_s: float = 5.0,
        recorded_sensor_ids: Collection[str] = DEFAULT_HISTORY_SENSOR_IDS,
    ) -> None:
        if retention_days < 1:
            raise ValueError("retention_days must be at least one")
        if max_mb < 1:
            raise ValueError("max_mb must be at least one")
        if flush_interval_s <= 0 or not math.isfinite(flush_interval_s):
            raise ValueError("flush_interval_s must be a positive finite number")
        self.database_path = Path(database_path)
        self.enabled = enabled
        self.retention_days = retention_days
        self.max_mb = max_mb
        self.recorded_sensor_ids = frozenset(recorded_sensor_ids)
        self.flush_interval_s = flush_interval_s
        self._max_bytes = max_mb * _MEBIBYTE
        self._batch_size = max(1, min(_MAX_PENDING_SAMPLES, self._max_bytes // 8192))
        self._pending: queue.Queue[HistorySample] = queue.Queue(
            maxsize=_MAX_PENDING_SAMPLES
        )
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._connection: sqlite3.Connection | None = None
        self._connection_lock = threading.RLock()
        self._record_lock = threading.Lock()
        self._last_recorded: dict[str, HistorySample] = {}
        self._status_lock = threading.RLock()
        self._warning: str | None = None
        self._dropped_samples = 0
        self._closed = False

        if enabled:
            try:
                self.database_path.parent.mkdir(parents=True, exist_ok=True)
                self._initialize()
            except (OSError, sqlite3.Error) as error:
                self._set_warning(f"History database could not be opened: {error}")
                _LOG.exception(
                    "History database initialization failed at %s",
                    self.database_path,
                )
            self._thread = threading.Thread(
                target=self._writer_loop,
                name="overpine-history-writer",
                daemon=True,
            )
            self._thread.start()

    def record(
        self,
        timestamp: float,
        sensor_id: str,
        value: float | None,
        state: str,
    ) -> None:
        """Queue one registry sample without performing synchronous disk I/O."""
        if not self.enabled or self._closed:
            return
        if (
            not math.isfinite(timestamp)
            or not sensor_id
            or (value is not None and not math.isfinite(value))
            or not state
        ):
            _LOG.warning("Rejected invalid sensor sample for SQLite history")
            return
        if (
            state == "NOT_INITIALIZED"
            or sensor_id not in self.recorded_sensor_ids
            or _is_detail_sensor(sensor_id)
        ):
            return
        sample = HistorySample(timestamp, sensor_id, value, state)
        with self._record_lock:
            previous = self._last_recorded.get(sensor_id)
            if previous is not None and not _should_record(previous, sample):
                return
            try:
                self._pending.put_nowait(sample)
            except queue.Full:
                try:
                    self._pending.get_nowait()
                    self._pending.task_done()
                except queue.Empty:
                    pass
                with self._status_lock:
                    self._dropped_samples += 1
                try:
                    self._pending.put_nowait(sample)
                except queue.Full:
                    self._set_warning(
                        "History writer is behind; the newest sample could not be queued"
                    )
                    return
            self._last_recorded[sensor_id] = sample

    def status(self) -> dict[str, Any]:
        """Return database size, configured limits, and any visible warning."""
        database_size = self._database_size()
        with self._status_lock:
            warning = self._warning
            dropped = self._dropped_samples
        if dropped:
            warning = (
                f"{warning}; {dropped} queued history samples were dropped"
                if warning
                else f"{dropped} queued history samples were dropped"
            )
        return {
            "enabled": self.enabled,
            "available": self.enabled and warning is None,
            "warning": warning,
            "database_size_bytes": database_size,
            "retention_days": self.retention_days,
            "max_mb": self.max_mb,
            "pending_samples": self._pending.qsize(),
        }

    def sensors(self) -> list[str]:
        """List sensor identifiers present in raw or downsampled history."""
        if not self.enabled:
            return []
        with self._connection_lock:
            connection = self._require_connection()
            try:
                rows = connection.execute(
                    "SELECT sensor_id FROM samples "
                    "UNION SELECT sensor_id FROM minute_samples "
                    "UNION SELECT sensor_id FROM aggregate_samples "
                    "ORDER BY sensor_id"
                ).fetchall()
            except sqlite3.Error as error:
                self._record_storage_error("Could not list history sensors", error)
                raise HistoryStorageError(str(error)) from error
        return [str(row[0]) for row in rows]

    def first_sample_timestamp(self) -> float | None:
        """Return the earliest timestamp retained in any history resolution."""
        with self._connection_lock:
            connection = self._connection
            owns_connection = False
            try:
                if connection is None and self.enabled:
                    connection = self._require_connection()
                elif connection is None:
                    if not self.database_path.is_file():
                        return None
                    connection = sqlite3.connect(
                        f"{self.database_path.resolve().as_uri()}?mode=ro",
                        uri=True,
                        timeout=1.0,
                    )
                    owns_connection = True
                assert connection is not None
                row = connection.execute(
                    "SELECT MIN(ts) FROM ("
                    "SELECT ts FROM samples UNION ALL "
                    "SELECT minute_ts FROM minute_samples UNION ALL "
                    "SELECT period_ts FROM aggregate_samples"
                    ")"
                ).fetchone()
            except sqlite3.Error as error:
                self._record_storage_error(
                    "Could not read the first history sample",
                    error,
                )
                raise HistoryStorageError(str(error)) from error
            finally:
                if owns_connection and connection is not None:
                    connection.close()
        return float(row[0]) if row is not None and row[0] is not None else None

    def read(
        self,
        sensor_id: str,
        start: float,
        end: float,
        resolution: str = "auto",
    ) -> dict[str, Any]:
        """Read raw or five-minute-aggregated points in a bounded time range."""
        if not self.enabled:
            raise HistoryStorageError("Sensor history is disabled in settings")
        if resolution not in _VALID_RESOLUTIONS:
            raise ValueError("resolution must be auto, raw, minute, or 5-minute")
        if not math.isfinite(start) or not math.isfinite(end) or start >= end:
            raise ValueError("History range must be finite and start before end")

        with self._connection_lock:
            connection = self._require_connection()
            return self._read_with_connection(
                connection,
                sensor_id,
                start,
                end,
                resolution,
            )

    def _read_with_connection(
        self,
        connection: sqlite3.Connection,
        sensor_id: str,
        start: float,
        end: float,
        resolution: str,
    ) -> dict[str, Any]:
        """Read a validated range while the connection lock is held."""
        now = time.time()
        raw_cutoff = now - _RAW_RETENTION_SECONDS
        minute_cutoff = now - _MINUTE_RETENTION_SECONDS
        selected_resolution = resolution
        if resolution == "auto":
            if end - start <= _RAW_RETENTION_SECONDS and start >= raw_cutoff:
                selected_resolution = "raw"
            elif end - start <= _MINUTE_RETENTION_SECONDS and start >= minute_cutoff:
                selected_resolution = "minute"
            else:
                selected_resolution = "5-minute"
        try:
            if selected_resolution == "raw":
                rows = connection.execute(
                    "SELECT ts, value, value, value, state, 'raw' FROM samples "
                    "WHERE sensor_id = ? AND ts >= ? AND ts <= ? "
                    "ORDER BY ts",
                    (sensor_id, start, end),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT period_ts, average, minimum, maximum, state, "
                    "'five_minute' FROM aggregate_samples "
                    "WHERE sensor_id = ? AND period_ts >= ? AND period_ts <= ? "
                    "UNION ALL "
                    "SELECT minute_ts, average, minimum, maximum, state, "
                    "'minute' FROM minute_samples "
                    "WHERE sensor_id = ? AND minute_ts >= ? AND minute_ts <= ? "
                    "UNION ALL "
                    "SELECT ts, value, value, value, state, 'raw' FROM samples "
                    "WHERE sensor_id = ? AND ts >= ? AND ts <= ? "
                    "ORDER BY 1",
                    (
                        sensor_id,
                        start,
                        end,
                        sensor_id,
                        start,
                        end,
                        sensor_id,
                        max(start, raw_cutoff),
                        end,
                    ),
                ).fetchall()
        except sqlite3.Error as error:
            self._record_storage_error("Could not read sensor history", error)
            raise HistoryStorageError(str(error)) from error
        return {
            "sensor_id": sensor_id,
            "resolution": selected_resolution,
            "samples": [
                {
                    "ts": float(row[0]),
                    "value": float(row[1]) if row[1] is not None else None,
                    "min": float(row[2]) if row[2] is not None else None,
                    "max": float(row[3]) if row[3] is not None else None,
                    "state": str(row[4]),
                    "source": str(row[5]),
                }
                for row in rows
            ],
        }

    def clear(self) -> None:
        """Remove all stored samples while leaving the SQLite schema intact."""
        if not self.enabled:
            return
        with self._connection_lock:
            connection = self._require_connection()
            try:
                while True:
                    try:
                        self._pending.get_nowait()
                    except queue.Empty:
                        break
                    self._pending.task_done()
                connection.execute("BEGIN")
                connection.execute("DELETE FROM samples")
                connection.execute("DELETE FROM minute_samples")
                connection.execute("DELETE FROM aggregate_samples")
                connection.commit()
                self._reclaim_space(connection)
                with self._record_lock:
                    self._last_recorded.clear()
            except sqlite3.Error as error:
                connection.rollback()
                self._record_storage_error("Could not clear sensor history", error)
                raise HistoryStorageError(str(error)) from error

    def close(self) -> None:
        """Flush queued samples, stop the writer, and close SQLite cleanly."""
        if self._closed:
            return
        self._closed = True
        if self._thread is not None:
            self._stop_event.set()
            self._thread.join(timeout=max(5.0, self.flush_interval_s + 2.0))
            if self._thread.is_alive():
                self._set_warning("History writer did not stop before its timeout")
                _LOG.error("History writer did not stop before shutdown timeout")
        with self._connection_lock:
            if self._connection is not None:
                try:
                    self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    self._connection.close()
                except sqlite3.Error as error:
                    self._record_storage_error(
                        "Could not close the history database cleanly",
                        error,
                    )
                finally:
                    self._connection = None

    def _initialize(self) -> None:
        """Create or migrate the schema and compact the existing database."""
        with self._connection_lock:
            connection = sqlite3.connect(
                self.database_path,
                timeout=1.0,
                check_same_thread=False,
            )
            try:
                connection.execute("PRAGMA busy_timeout = 1000")
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = NORMAL")
                connection.execute("PRAGMA auto_vacuum = INCREMENTAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS samples (
                        sample_id INTEGER PRIMARY KEY,
                        ts REAL NOT NULL,
                        sensor_id TEXT NOT NULL,
                        value REAL,
                        state TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS samples_sensor_time
                        ON samples(sensor_id, ts);
                    CREATE INDEX IF NOT EXISTS samples_time
                        ON samples(ts);
                    CREATE TABLE IF NOT EXISTS minute_samples (
                        minute_ts INTEGER NOT NULL,
                        sensor_id TEXT NOT NULL,
                        average REAL,
                        minimum REAL,
                        maximum REAL,
                        state TEXT NOT NULL,
                        sample_count INTEGER NOT NULL,
                        PRIMARY KEY(minute_ts, sensor_id)
                    );
                    CREATE INDEX IF NOT EXISTS minute_samples_sensor_time
                        ON minute_samples(sensor_id, minute_ts);
                    CREATE TABLE IF NOT EXISTS aggregate_samples (
                        period_ts INTEGER NOT NULL,
                        sensor_id TEXT NOT NULL,
                        average REAL,
                        minimum REAL,
                        maximum REAL,
                        state TEXT NOT NULL,
                        sample_count INTEGER NOT NULL,
                        PRIMARY KEY(period_ts, sensor_id)
                    );
                    CREATE INDEX IF NOT EXISTS aggregate_samples_sensor_time
                        ON aggregate_samples(sensor_id, period_ts);
                    """
                )
                self._migrate_schema(connection)
                now = time.time()
                self._downsample_old_samples(
                    connection,
                    now - _RAW_RETENTION_SECONDS,
                )
                self._downsample_old_minute_samples(
                    connection,
                    now - _MINUTE_RETENTION_SECONDS,
                )
                self._prune_by_age(connection, now)
                connection.commit()
                if self._database_size() > self._max_bytes:
                    self._prune_by_size(connection)
                self._reclaim_space(connection)
                self._connection = connection
                self._load_last_recorded(connection)
                self._set_max_page_count(connection)
            except sqlite3.Error:
                connection.close()
                raise

    def _migrate_schema(self, connection: sqlite3.Connection) -> None:
        """Upgrade existing databases to retain one-minute aggregates."""
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version > _SCHEMA_VERSION:
            raise sqlite3.DatabaseError(
                f"History schema version {version} is newer than supported"
            )
        if version < _SCHEMA_VERSION:
            connection.execute("PRAGMA user_version = 2")

    def _writer_loop(self) -> None:
        """Flush queued rows at a fixed cadence and keep failures off the scheduler."""
        next_flush = time.monotonic() + self.flush_interval_s
        while not self._stop_event.is_set():
            wait_s = max(0.0, next_flush - time.monotonic())
            self._stop_event.wait(wait_s)
            if self._stop_event.is_set() or time.monotonic() >= next_flush:
                self._flush_pending()
                next_flush = time.monotonic() + self.flush_interval_s
        self._flush_pending()

    def _flush_pending(self) -> None:
        """Write one queued batch in one transaction, then prune and checkpoint."""
        if self._pending.empty() or not self.enabled:
            return
        with self._connection_lock:
            batch: list[HistorySample] = []
            try:
                connection = self._require_connection()
                pending_count = min(self._pending.qsize(), self._batch_size)
                projected_bytes = pending_count * 2048
                size_cap_hit = (
                    pending_count > 0
                    and self._database_size() + projected_bytes > self._max_bytes
                )
                if size_cap_hit:
                    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    reserve = max(64 * 1024, self._max_bytes // 16)
                    target_bytes = max(
                        1,
                        self._max_bytes - projected_bytes - reserve,
                    )
                    self._prune_by_size(connection, target_bytes)
                    self._reclaim_space(connection)
                while len(batch) < self._batch_size:
                    try:
                        batch.append(self._pending.get_nowait())
                    except queue.Empty:
                        break
                if not batch:
                    return
                try:
                    connection.execute("BEGIN")
                    connection.executemany(
                        "INSERT INTO samples(ts, sensor_id, value, state) "
                        "VALUES (?, ?, ?, ?)",
                        [
                            (
                                sample.timestamp,
                                sample.sensor_id,
                                sample.value,
                                sample.state,
                            )
                            for sample in batch
                        ],
                    )
                    self._downsample_old_samples(
                        connection,
                        time.time() - _RAW_RETENTION_SECONDS,
                    )
                    now = time.time()
                    self._downsample_old_minute_samples(
                        connection,
                        now - _MINUTE_RETENTION_SECONDS,
                    )
                    self._prune_by_age(connection, now)
                    connection.commit()
                except (OSError, sqlite3.Error, HistoryStorageError):
                    connection.rollback()
                    for sample in batch:
                        self._pending.task_done()
                    for sample in reversed(batch):
                        try:
                            self._pending.put_nowait(sample)
                        except queue.Full:
                            with self._status_lock:
                                self._dropped_samples += 1
                    raise
                for _ in batch:
                    self._pending.task_done()

                try:
                    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    if self._database_size() > self._max_bytes:
                        self._prune_by_size(connection)
                        self._reclaim_space(connection)
                    self._set_max_page_count(connection)
                except (OSError, sqlite3.Error, HistoryStorageError) as error:
                    self._record_storage_error(
                        "Could not maintain the history database size",
                        error,
                    )
                    return

                if (
                    self._dropped_samples == 0
                    and self._database_size() <= self._max_bytes
                ):
                    with self._status_lock:
                        self._warning = None
            except (OSError, sqlite3.Error, HistoryStorageError) as error:
                self._record_storage_error("Could not write sensor history", error)

    def _downsample_old_samples(
        self,
        connection: sqlite3.Connection,
        cutoff: float,
    ) -> None:
        """Move old raw readings into one-minute buckets without losing gaps."""
        rows = connection.execute(
            "SELECT sample_id, ts, sensor_id, value, state FROM samples "
            "WHERE ts < ? ORDER BY ts",
            (cutoff,),
        ).fetchall()
        buckets: dict[tuple[int, str], list[tuple[float | None, str]]] = {}
        sample_ids: list[int] = []
        for sample_id, timestamp, sensor_id, value, state in rows:
            minute_ts = int(float(timestamp) // _MINUTE_INTERVAL_SECONDS)
            minute_ts *= _MINUTE_INTERVAL_SECONDS
            buckets.setdefault((minute_ts, str(sensor_id)), []).append(
                (float(value) if value is not None else None, str(state))
            )
            sample_ids.append(int(sample_id))
        for (minute_ts, sensor_id), samples in buckets.items():
            summary = _summarize_raw_samples(samples)
            self._upsert_aggregate(
                connection,
                "minute_samples",
                "minute_ts",
                minute_ts,
                sensor_id,
                summary,
            )
        if sample_ids:
            connection.executemany(
                "DELETE FROM samples WHERE sample_id = ?",
                [(sample_id,) for sample_id in sample_ids],
            )

    def _downsample_old_minute_samples(
        self,
        connection: sqlite3.Connection,
        cutoff: float,
    ) -> None:
        """Roll old one-minute buckets into five-minute history."""
        boundary = _aggregate_cutoff(cutoff)
        rows = connection.execute(
            "SELECT minute_ts, sensor_id, average, minimum, maximum, state, "
            "sample_count FROM minute_samples WHERE minute_ts < ? "
            "ORDER BY minute_ts",
            (boundary,),
        ).fetchall()
        buckets: dict[
            tuple[int, str],
            list[tuple[float | None, float | None, float | None, str, int]],
        ] = {}
        keys: list[tuple[int, str]] = []
        for minute_ts, sensor_id, average, minimum, maximum, state, count in rows:
            period_ts = _aggregate_cutoff(float(minute_ts))
            key = (period_ts, str(sensor_id))
            buckets.setdefault(key, []).append(
                (
                    float(average) if average is not None else None,
                    float(minimum) if minimum is not None else None,
                    float(maximum) if maximum is not None else None,
                    str(state),
                    int(count),
                )
            )
            keys.append((int(minute_ts), str(sensor_id)))
        for (period_ts, sensor_id), samples in buckets.items():
            summary = _summarize_aggregate_samples(samples)
            self._upsert_aggregate(
                connection,
                "aggregate_samples",
                "period_ts",
                period_ts,
                sensor_id,
                summary,
            )
        if keys:
            connection.executemany(
                "DELETE FROM minute_samples WHERE minute_ts = ? AND sensor_id = ?",
                keys,
            )

    @staticmethod
    def _upsert_aggregate(
        connection: sqlite3.Connection,
        table: str,
        timestamp_column: str,
        timestamp: int,
        sensor_id: str,
        summary: tuple[float | None, float | None, float | None, str, int],
    ) -> None:
        average, minimum, maximum, state, sample_count = summary
        connection.execute(
            f"INSERT INTO {table}("
            f"{timestamp_column}, sensor_id, average, minimum, maximum, state, "
            "sample_count) VALUES (?, ?, ?, ?, ?, ?, ?) "
            f"ON CONFLICT({timestamp_column}, sensor_id) DO UPDATE SET "
            "average=CASE "
            "WHEN excluded.average IS NULL THEN "
            f"{table}.average "
            "WHEN "
            f"{table}.average IS NULL THEN excluded.average "
            "ELSE ("
            f"{table}.average * {table}.sample_count "
            "+ excluded.average * excluded.sample_count"
            ") / NULLIF("
            f"{table}.sample_count + excluded.sample_count, 0) END, "
            "minimum=CASE "
            "WHEN excluded.minimum IS NULL THEN "
            f"{table}.minimum "
            "WHEN "
            f"{table}.minimum IS NULL THEN excluded.minimum "
            "ELSE MIN("
            f"{table}.minimum, excluded.minimum) END, "
            "maximum=CASE "
            "WHEN excluded.maximum IS NULL THEN "
            f"{table}.maximum "
            "WHEN "
            f"{table}.maximum IS NULL THEN excluded.maximum "
            "ELSE MAX("
            f"{table}.maximum, excluded.maximum) END, "
            f"state=CASE WHEN {table}.state = excluded.state "
            f"THEN {table}.state ELSE 'UNAVAILABLE' END, "
            f"sample_count={table}.sample_count + excluded.sample_count",
            (
                timestamp,
                sensor_id,
                average,
                minimum,
                maximum,
                state,
                sample_count,
            ),
        )

    def _prune_by_age(
        self,
        connection: sqlite3.Connection,
        now: float,
    ) -> None:
        """Delete raw and aggregate rows outside the configured age range."""
        cutoff = now - self.retention_days * 86400
        connection.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
        connection.execute(
            "DELETE FROM minute_samples WHERE minute_ts < ?",
            (cutoff,),
        )
        connection.execute(
            "DELETE FROM aggregate_samples WHERE period_ts < ?",
            (cutoff,),
        )

    def _prune_by_size(
        self,
        connection: sqlite3.Connection,
        target_bytes: int | None = None,
    ) -> None:
        """Remove oldest stored readings until compacted pages fit the cap."""
        target = self._max_bytes if target_bytes is None else target_bytes
        while self._compacted_database_size(connection) > target:
            raw_rows = connection.execute(
                "SELECT sample_id, ts FROM samples ORDER BY ts LIMIT ?",
                (_PRUNE_BATCH_SIZE,),
            ).fetchall()
            minute_rows = connection.execute(
                "SELECT minute_ts, sensor_id FROM minute_samples "
                "ORDER BY minute_ts LIMIT ?",
                (_PRUNE_BATCH_SIZE,),
            ).fetchall()
            aggregate_rows = connection.execute(
                "SELECT period_ts, sensor_id FROM aggregate_samples "
                "ORDER BY period_ts LIMIT ?",
                (_PRUNE_BATCH_SIZE,),
            ).fetchall()
            candidates = [
                (float(timestamp), "samples", row_id)
                for row_id, timestamp in raw_rows
            ] + [
                (float(minute_ts), "minute_samples", str(sensor_id))
                for minute_ts, sensor_id in minute_rows
            ] + [
                (float(period_ts), "aggregate_samples", str(sensor_id))
                for period_ts, sensor_id in aggregate_rows
            ]
            if not candidates:
                self._set_warning(
                    "History database overhead prevents reserving space under the size cap"
                )
                return
            candidates.sort()
            oldest = candidates[:_PRUNE_BATCH_SIZE]
            raw_ids = [
                int(candidate[2])
                for candidate in oldest
                if candidate[1] == "samples"
            ]
            minute_keys = [
                (int(candidate[0]), str(candidate[2]))
                for candidate in oldest
                if candidate[1] == "minute_samples"
            ]
            aggregate_keys = [
                (int(candidate[0]), str(candidate[2]))
                for candidate in oldest
                if candidate[1] == "aggregate_samples"
            ]
            if raw_ids:
                connection.executemany(
                    "DELETE FROM samples WHERE sample_id = ?",
                    [(row_id,) for row_id in raw_ids],
                )
            if minute_keys:
                connection.executemany(
                    "DELETE FROM minute_samples "
                    "WHERE minute_ts = ? AND sensor_id = ?",
                    minute_keys,
                )
            if aggregate_keys:
                connection.executemany(
                    "DELETE FROM aggregate_samples "
                    "WHERE period_ts = ? AND sensor_id = ?",
                    aggregate_keys,
                )
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if self._compacted_database_size(connection) > target:
            self._set_warning(
                "History database could not be reduced below its configured size cap"
            )

    def _compacted_database_size(self, connection: sqlite3.Connection) -> int:
        """Estimate file bytes after reclaiming free pages, plus any WAL."""
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        freelist_count = int(
            connection.execute("PRAGMA freelist_count").fetchone()[0]
        )
        wal_path = Path(str(self.database_path) + "-wal")
        try:
            wal_size = wal_path.stat().st_size
        except FileNotFoundError:
            wal_size = 0
        return max(0, page_count - freelist_count) * page_size + wal_size

    def _reclaim_space(self, connection: sqlite3.Connection) -> None:
        """Truncate the WAL and rebuild the database without free pages."""
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA auto_vacuum = INCREMENTAL")
        connection.execute("VACUUM")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def _load_last_recorded(self, connection: sqlite3.Connection) -> None:
        """Seed change filtering from existing raw rows for selected sensors."""
        for sensor_id in self.recorded_sensor_ids:
            if _is_detail_sensor(sensor_id):
                continue
            row = connection.execute(
                "SELECT ts, value, state FROM samples "
                "WHERE sensor_id = ? ORDER BY ts DESC, sample_id DESC LIMIT 1",
                (sensor_id,),
            ).fetchone()
            if row is not None:
                self._last_recorded[sensor_id] = HistorySample(
                    float(row[0]),
                    sensor_id,
                    float(row[1]) if row[1] is not None else None,
                    str(row[2]),
                )

    def _set_max_page_count(self, connection: sqlite3.Connection) -> None:
        """Apply a SQLite page limit as a second guard against database growth."""
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        max_pages = max(1, self._max_bytes // max(page_size, 1))
        connection.execute(f"PRAGMA max_page_count = {max_pages}")

    def _require_connection(self) -> sqlite3.Connection:
        """Return the usable connection, retrying initialization if needed."""
        if self._connection is None:
            try:
                self.database_path.parent.mkdir(parents=True, exist_ok=True)
                self._initialize()
            except (OSError, sqlite3.Error) as error:
                self._set_warning(f"History database is unavailable: {error}")
                _LOG.exception("Could not reopen history database")
                raise HistoryStorageError(str(error)) from error
        assert self._connection is not None
        return self._connection

    def _database_size(self) -> int:
        """Count persistent database and WAL bytes, excluding transient SHM."""
        total = 0
        for suffix in ("", "-wal"):
            try:
                total += Path(str(self.database_path) + suffix).stat().st_size
            except FileNotFoundError:
                continue
            except OSError as error:
                self._set_warning(f"Could not inspect history database size: {error}")
                _LOG.warning("Could not inspect history database size: %s", error)
                break
        return total

    def _record_storage_error(self, action: str, error: BaseException) -> None:
        """Expose a storage failure in status and logs without stopping monitoring."""
        message = f"{action}: {error}"
        self._set_warning(message)
        _LOG.exception("%s", message)

    def _set_warning(self, message: str) -> None:
        with self._status_lock:
            self._warning = message


def _aggregate_cutoff(timestamp: float) -> int:
    """Return the start of the five-minute bucket containing a timestamp."""
    return int(timestamp // _AGGREGATE_INTERVAL_SECONDS * _AGGREGATE_INTERVAL_SECONDS)


def _is_detail_sensor(sensor_id: str) -> bool:
    """Reject high-cardinality process, device, and counter-instance rows."""
    normalized = sensor_id.casefold()
    segments = normalized.split(".")
    return (
        any(
            segment in {"counter", "counters", "process", "processes", "pid"}
            for segment in segments
        )
        or normalized.startswith(_DETAIL_SENSOR_PREFIXES)
    )


def _should_record(previous: HistorySample, sample: HistorySample) -> bool:
    """Store meaningful changes, state transitions, or a ten-second heartbeat."""
    if sample.state != previous.state:
        return True
    if sample.timestamp - previous.timestamp >= _HEARTBEAT_SECONDS:
        return True
    if sample.value is None or previous.value is None:
        return sample.value != previous.value
    return abs(sample.value - previous.value) > _VALUE_CHANGE_THRESHOLD


def _summarize_raw_samples(
    samples: list[tuple[float | None, str]],
) -> tuple[float | None, float | None, float | None, str, int]:
    available = [
        value
        for value, state in samples
        if state == "AVAILABLE" and value is not None
    ]
    states = {state for _, state in samples}
    state = (
        "AVAILABLE"
        if len(available) == len(samples)
        else next(iter(states))
        if len(states) == 1 and "AVAILABLE" not in states
        else "UNAVAILABLE"
    )
    return (
        sum(available) / len(available) if available else None,
        min(available) if available else None,
        max(available) if available else None,
        state,
        len(samples),
    )


def _summarize_aggregate_samples(
    samples: list[
        tuple[float | None, float | None, float | None, str, int]
    ],
) -> tuple[float | None, float | None, float | None, str, int]:
    available = [
        sample
        for sample in samples
        if sample[3] == "AVAILABLE" and sample[0] is not None
    ]
    states = {sample[3] for sample in samples}
    state = (
        "AVAILABLE"
        if len(available) == len(samples)
        else next(iter(states))
        if len(states) == 1 and "AVAILABLE" not in states
        else "UNAVAILABLE"
    )
    value_count = sum(sample[4] for sample in available)
    average = (
        sum(float(sample[0]) * sample[4] for sample in available) / value_count
        if value_count
        else None
    )
    minimums = [sample[1] for sample in available if sample[1] is not None]
    maximums = [sample[2] for sample in available if sample[2] is not None]
    return (
        average,
        min(minimums) if minimums else None,
        max(maximums) if maximums else None,
        state,
        sum(sample[4] for sample in samples),
    )
