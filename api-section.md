## Logs and diagnostics

Both endpoints are read-only `GET` requests and use the same Host and Origin checks as the other routes.

### `GET /api/logs?level=DEBUG&q=text&limit=200`

Returns the newest entries of the application log file (`[logging] file`, default `logs/overpine.log`). Only the last 512 KiB of the file is read. Lines that continue an entry, such as a traceback, are attached to that entry.

Query parameters (each may appear once):

- `level`: minimum severity, one of `DEBUG` (default), `INFO`, `WARNING`, `ERROR`, `CRITICAL`.
- `q`: case-insensitive text searched in the logger name and message, at most 100 characters.
- `limit`: number of entries to return, `1..500`, default `200`. The newest matching entries are kept.

Invalid values return `422`. The response is:

```json
{
  "available": true,
  "message": null,
  "log_file": "C:\\...\\logs\\overpine.log",
  "entries": [
    {"time": "2026-11-13 10:00:01,002", "level": "WARNING", "logger": "overpine", "message": "..."}
  ],
  "total_matching": 832,
  "limit": 200
}
```

If the file is missing, unreadable, or configured outside the app data folder, `available` is `false`, `entries` is empty, and `message` explains why.

### `GET /api/diagnostics`

Returns a snapshot for troubleshooting: `generated_at`, `app`, `python_version`, `system`, `monitoring_state`, `session_duration_s`, `sensor_counts` (per state, excluding detail sensors), `detail_sensor_count` (per-process and per-counter rows, not counted), `problems` (sensors that are not `AVAILABLE`, each with `id`, `name`, `category`, `state` and `message`), `providers` (`name`, `state`, `last_error`, `backoff_s`, `sensor_count`), `history` (the same status object as `/api/history/sensors`), and `log_file_size_bytes`. No value is simulated: a sensor without a reading appears in `problems` with its reason.

