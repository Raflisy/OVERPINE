"""Tests for bounded, batched SQLite sensor history."""
from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.api.routes.history import clear_history, list_history_sensors
from backend.monitoring.models import Sensor
from backend.storage import history as history_module
from backend.storage.history import HistoryStore
from backend.web.router import HTTPError, Request


class HistoryStoreTests(unittest.TestCase):
    """Exercise history storage with isolated temporary SQLite files."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "history.db"
        self.store = HistoryStore(
            self.database_path,
            flush_interval_s=60,
            recorded_sensor_ids={
                "cpu.load",
                "cpu.temp",
                "battery.charge",
                "cpu.package.temperature",
                "cpu.core.0",
                "process.cpu",
                "power.power.main",
            },
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def test_batch_write_and_read_back(self) -> None:
        timestamp = time.time() - 2
        self.store.record(timestamp, "cpu.load", 25.5, "AVAILABLE")
        self.store.record(timestamp + 1, "cpu.load", 30.0, "AVAILABLE")

        self.store._flush_pending()

        result = self.store.read("cpu.load", timestamp, timestamp + 1, "raw")
        self.assertEqual(
            [sample["value"] for sample in result["samples"]],
            [25.5, 30.0],
        )
        self.assertEqual(
            [sample["source"] for sample in result["samples"]],
            ["raw", "raw"],
        )
        self.assertEqual(self.store._pending.unfinished_tasks, 0)

    def test_first_sample_timestamp_reads_existing_db_when_recording_is_disabled(
        self,
    ) -> None:
        timestamp = time.time() - 2
        self.store.record(timestamp, "cpu.load", 25.5, "AVAILABLE")
        self.store._flush_pending()
        self.store.close()
        self.store = HistoryStore(
            self.database_path,
            enabled=False,
            recorded_sensor_ids={"cpu.load"},
        )

        first_sample = self.store.first_sample_timestamp()
        self.assertIsNotNone(first_sample)
        assert first_sample is not None
        self.assertAlmostEqual(first_sample, timestamp)

    def test_null_value_and_state_are_preserved(self) -> None:
        timestamp = time.time() - 2
        self.store.record(timestamp, "cpu.temp", None, "UNAVAILABLE")
        self.store._flush_pending()

        sample = self.store.read(
            "cpu.temp",
            timestamp,
            timestamp + 0.001,
            "raw",
        )["samples"][0]
        self.assertIsNone(sample["value"])
        self.assertEqual(sample["state"], "UNAVAILABLE")
        self.assertEqual(sample["source"], "raw")

    def test_uninitialized_and_detail_sensors_are_not_recorded_even_if_selected(
        self,
    ) -> None:
        timestamp = time.time() - 2
        self.store.record(timestamp, "cpu.load", 10.0, "NOT_INITIALIZED")
        self.store.record(timestamp, "cpu.core.0", 10.0, "AVAILABLE")
        self.store.record(timestamp, "process.cpu", 10.0, "AVAILABLE")
        self.store.record(timestamp, "power.power.main", 10.0, "AVAILABLE")
        self.store.record(timestamp, "cpu.other", 10.0, "AVAILABLE")

        self.assertEqual(self.store._pending.qsize(), 0)

    def test_record_suppresses_small_changes_until_heartbeat(self) -> None:
        timestamp = time.time() - 20
        self.store.record(timestamp, "cpu.load", 10.0, "AVAILABLE")
        self.store.record(timestamp + 1, "cpu.load", 10.005, "AVAILABLE")
        self.store.record(timestamp + 2, "cpu.load", 10.02, "AVAILABLE")
        self.store.record(timestamp + 12, "cpu.load", 10.021, "AVAILABLE")

        self.store._flush_pending()

        samples = self.store.read(
            "cpu.load",
            timestamp,
            timestamp + 12,
            "raw",
        )["samples"]
        self.assertEqual(
            [(sample["ts"], sample["value"]) for sample in samples],
            [
                (timestamp, 10.0),
                (timestamp + 2, 10.02),
                (timestamp + 12, 10.021),
            ],
        )

    def test_state_change_is_immediate_and_preserves_null(self) -> None:
        timestamp = time.time() - 20
        self.store.record(timestamp, "cpu.temp", 35.0, "AVAILABLE")
        self.store.record(timestamp + 1, "cpu.temp", None, "UNAVAILABLE")
        self.store.record(timestamp + 2, "cpu.temp", None, "UNAVAILABLE")

        self.store._flush_pending()

        samples = self.store.read(
            "cpu.temp",
            timestamp,
            timestamp + 2,
            "raw",
        )["samples"]
        self.assertEqual(len(samples), 2)
        self.assertEqual(samples[1]["state"], "UNAVAILABLE")
        self.assertIsNone(samples[1]["value"])

    def test_raw_retention_rolls_samples_to_minute_at_two_minutes(self) -> None:
        now = time.time()
        connection = self.store._require_connection()
        connection.executemany(
            "INSERT INTO samples(ts, sensor_id, value, state) "
            "VALUES (?, ?, ?, ?)",
            [
                (now - 121, "cpu.load", 10.0, "AVAILABLE"),
                (now - 119, "cpu.load", 11.0, "AVAILABLE"),
            ],
        )
        self.store._downsample_old_samples(connection, now - 120)
        connection.commit()

        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM samples WHERE sensor_id='cpu.load'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM minute_samples WHERE sensor_id='cpu.load'"
            ).fetchone()[0],
            1,
        )

    def test_downsampling_calculates_minute_average_minimum_and_maximum(
        self,
    ) -> None:
        period = (int(time.time() // 300) - 1) * 300
        self.store.record(period, "cpu.load", 10.0, "AVAILABLE")
        self.store.record(period + 10, "cpu.load", 20.0, "AVAILABLE")
        self.store.record(period + 20, "cpu.load", 30.0, "AVAILABLE")

        self.store._flush_pending()

        samples = self.store.read(
            "cpu.load",
            period,
            period + 59,
            "minute",
        )["samples"]
        self.assertEqual(
            samples,
            [{
                "ts": float(period),
                "value": 20.0,
                "min": 10.0,
                "max": 30.0,
                "state": "AVAILABLE",
                "source": "minute",
            }],
        )

    def test_downsampling_keeps_missing_values_null_and_state(self) -> None:
        period = (int(time.time() // 300) - 3) * 300
        self.store.record(period, "cpu.temp", None, "ERROR")
        self.store.record(period + 10, "cpu.temp", None, "ERROR")

        self.store._flush_pending()

        sample = self.store.read(
            "cpu.temp",
            period,
            period + 299,
            "5-minute",
        )["samples"][0]
        self.assertIsNone(sample["value"])
        self.assertIsNone(sample["min"])
        self.assertIsNone(sample["max"])
        self.assertEqual(sample["state"], "ERROR")

    def test_downsampling_merges_with_migrated_bucket(self) -> None:
        period = (int(time.time() // 300) - 4) * 300
        connection = self.store._require_connection()
        connection.execute(
            "INSERT INTO aggregate_samples("
            "period_ts, sensor_id, average, minimum, maximum, state, sample_count"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (period, "cpu.load", 10.0, 8.0, 12.0, "AVAILABLE", 2),
        )
        connection.commit()
        self.store.record(period + 10, "cpu.load", 20.0, "AVAILABLE")
        self.store.record(period + 20, "cpu.load", 30.0, "AVAILABLE")

        self.store._flush_pending()

        sample = self.store.read(
            "cpu.load",
            period,
            period + 299,
            "5-minute",
        )["samples"][0]
        self.assertEqual(sample["value"], 17.5)
        self.assertEqual(sample["min"], 8.0)
        self.assertEqual(sample["max"], 30.0)
        self.assertEqual(sample["state"], "AVAILABLE")

    def test_legacy_minute_aggregates_migrate_without_dropping_state(self) -> None:
        self.store.close()
        period = (int(time.time() // 300) - 4) * 300
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("DROP TABLE minute_samples")
            connection.execute(
                "CREATE TABLE minute_samples("
                "minute_ts INTEGER NOT NULL, sensor_id TEXT NOT NULL, "
                "average REAL, minimum REAL, maximum REAL, state TEXT NOT NULL, "
                "sample_count INTEGER NOT NULL, PRIMARY KEY(minute_ts, sensor_id))"
            )
            connection.executemany(
                "INSERT INTO minute_samples VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (period, "cpu.load", 10.0, 8.0, 12.0, "AVAILABLE", 3),
                    (period + 60, "cpu.load", 20.0, 15.0, 25.0, "AVAILABLE", 5),
                    (period + 120, "cpu.load", None, None, None, "ERROR", 1),
                ],
            )
            connection.commit()
        finally:
            connection.close()

        self.store = HistoryStore(self.database_path, flush_interval_s=60)
        samples = self.store.read(
            "cpu.load",
            period,
            period + 299,
            "5-minute",
        )["samples"]

        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["value"], 16.25)
        self.assertEqual(samples[0]["min"], 8.0)
        self.assertEqual(samples[0]["max"], 25.0)
        self.assertEqual(samples[0]["state"], "UNAVAILABLE")
        self.assertEqual(samples[0]["source"], "five_minute")
        self.assertEqual(
            self.store._require_connection()
            .execute(
                "SELECT COUNT(*) FROM minute_samples"
            )
            .fetchone()[0],
            0,
        )

    def test_existing_database_migrates_and_reclaims_freelist(self) -> None:
        self.store.close()
        connection = sqlite3.connect(self.database_path)
        try:
            connection.executemany(
                "INSERT INTO aggregate_samples("
                "period_ts, sensor_id, average, minimum, maximum, state, sample_count"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (index * 300, f"sensor.{index}", 1.0, 1.0, 1.0, "AVAILABLE", 1)
                    for index in range(1200)
                ],
            )
            connection.execute("PRAGMA user_version = 1")
            connection.commit()
            connection.execute("DELETE FROM aggregate_samples")
            connection.commit()
            freelist_before = connection.execute(
                "PRAGMA freelist_count"
            ).fetchone()[0]
        finally:
            connection.close()

        self.store = HistoryStore(
            self.database_path,
            flush_interval_s=60,
            recorded_sensor_ids={"cpu.load"},
        )

        connection = self.store._require_connection()
        self.assertGreater(freelist_before, 0)
        self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
        self.assertEqual(connection.execute("PRAGMA auto_vacuum").fetchone()[0], 2)
        self.assertEqual(connection.execute("PRAGMA freelist_count").fetchone()[0], 0)
        self.assertIsNotNone(
            connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='minute_samples'"
            ).fetchone()
        )

    def test_history_sensor_metadata_includes_real_name_and_unit(self) -> None:
        timestamp = time.time() - 2
        self.store.record(timestamp, "battery.charge", 75.0, "AVAILABLE")
        self.store._flush_pending()
        app = SimpleNamespace(
            history_store=self.store,
            monitoring_scheduler=SimpleNamespace(
                registry=SimpleNamespace(
                    list=lambda: [
                        Sensor(
                            "battery.charge",
                            "Battery charge",
                            "battery",
                            "%",
                            "test",
                        )
                    ]
                )
            ),
        )
        request = Request(
            method="GET",
            path="/api/history/sensors",
            query={},
            headers={},
            body=None,
            client_address=("127.0.0.1", 50000),
            app=app,
        )

        self.assertEqual(
            list_history_sensors(request)["sensors"],
            [{
                "id": "battery.charge",
                "name": "Battery charge",
                "unit": "%",
                "category": "battery",
            }],
        )

    def test_auto_range_reports_raw_and_five_minute_point_sources(self) -> None:
        period = (int(time.time() // 300) - 3) * 300
        now = time.time()
        self.store.record(period, "cpu.load", 10.0, "AVAILABLE")
        self.store.record(now, "cpu.load", 20.0, "AVAILABLE")
        self.store._flush_pending()

        result = self.store.read("cpu.load", period, now, "auto")

        self.assertEqual(result["resolution"], "5-minute")
        self.assertEqual(
            [sample["source"] for sample in result["samples"]],
            ["five_minute", "raw"],
        )

    def test_pruning_removes_rows_older_than_retention(self) -> None:
        self.store.close()
        self.store = HistoryStore(
            self.database_path,
            retention_days=1,
            flush_interval_s=60,
            recorded_sensor_ids={"cpu.load"},
        )
        now = time.time()
        old_timestamp = now - 2 * 86400
        self.store.record(old_timestamp, "cpu.load", 10.0, "AVAILABLE")
        self.store.record(now, "cpu.load", 20.0, "AVAILABLE")

        self.store._flush_pending()

        old_rows = self.store.read(
            "cpu.load",
            old_timestamp,
            old_timestamp + 1,
            "raw",
        )["samples"]
        self.assertEqual(old_rows, [])

    def test_size_pruning_keeps_same_bucket_rows_for_other_sensors(self) -> None:
        connection = self.store._require_connection()
        timestamp = 1_700_000_000
        connection.executemany(
            "INSERT INTO aggregate_samples("
            "period_ts, sensor_id, average, minimum, maximum, state, sample_count"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (timestamp, "sensor.a", 1.0, 1.0, 1.0, "AVAILABLE", 1),
                (timestamp, "sensor.b", 2.0, 2.0, 2.0, "AVAILABLE", 1),
            ],
        )
        connection.commit()
        self.store._max_bytes = 1

        with (
            patch.object(history_module, "_PRUNE_BATCH_SIZE", 1),
            patch.object(
                self.store,
                "_compacted_database_size",
                side_effect=[2, 0, 0],
            ),
        ):
            self.store._prune_by_size(connection)

        remaining = connection.execute(
            "SELECT sensor_id FROM aggregate_samples"
        ).fetchall()
        self.assertEqual(remaining, [("sensor.b",)])

    def test_corrupt_database_reports_warning_without_raising(self) -> None:
        self.store.close()
        self.database_path.write_bytes(b"not a sqlite database")

        with self.assertLogs("overpine.history", level="ERROR"):
            self.store = HistoryStore(
                self.database_path,
                flush_interval_s=60,
            )

        self.assertIn("could not be opened", self.store.status()["warning"])

    def test_clear_requires_token(self) -> None:
        connection = self.store._require_connection()
        connection.execute(
            "INSERT INTO minute_samples VALUES (?, ?, ?, ?, ?, ?, ?)",
            (1_700_000_000, "cpu.load", 1.0, 1.0, 1.0, "AVAILABLE", 1),
        )
        connection.commit()
        app = SimpleNamespace(history_store=self.store, session_token="session-secret")
        request = Request(
            method="POST",
            path="/api/history/clear",
            query={},
            headers={},
            body=None,
            client_address=("127.0.0.1", 50000),
            app=app,
        )
        with self.assertRaises(HTTPError) as error:
            clear_history(request)
        self.assertEqual(error.exception.status, 401)

        request.headers = {"X-Overpine-Token": "session-secret"}
        self.assertTrue(clear_history(request)["cleared"])
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM minute_samples").fetchone()[0],
            0,
        )


class HistoryPageContractTests(unittest.TestCase):
    """Keep the browser history labels aligned with the API response shape."""

    def setUp(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.history_page = (
            root / "frontend" / "js" / "pages" / "history.js"
        ).read_text(
            encoding="utf-8"
        )
        self.line_chart = (
            root / "frontend" / "js" / "charts" / "line-chart.js"
        ).read_text(
            encoding="utf-8"
        )

    def test_history_status_counts_raw_averages_and_chart_points(self) -> None:
        self.assertIn('sample.source === "five_minute"', self.history_page)
        self.assertIn('sample.source === "minute"', self.history_page)
        self.assertIn("1-minute average", self.history_page)
        self.assertIn("5-minute average", self.history_page)
        self.assertIn("raw reading", self.history_page)
        self.assertIn("samples.length} chart point", self.history_page)

    def test_sensor_name_unit_and_unit_precision_feed_chart_statistics(self) -> None:
        self.assertIn(
            '`${sensor.name}${sensor.unit ? ` (${sensor.unit})` : ""}`',
            self.history_page,
        )
        self.assertIn('["%", 1]', self.history_page)
        self.assertIn('["MHz", 0]', self.history_page)
        self.assertIn("value.toFixed(decimals)", self.history_page)
        self.assertIn("label.textContent = series.label;", self.line_chart)
        self.assertIn(
            'formatValue(series, value)',
            self.line_chart,
        )


if __name__ == "__main__":
    unittest.main()
