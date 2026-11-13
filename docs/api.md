# OVERPINE local API

The API is served by the standard-library HTTP server on `127.0.0.1`. The
server accepts `localhost` and `127.0.0.1` Host values for its active port.
No CORS headers are sent. Cross-origin state-changing requests are rejected.
`POST`, `PUT`, and `DELETE` requests require the current session's
`X-Overpine-Token` header. JSON request bodies are limited to 64 KiB.

Errors use this shape:

```json
{"error":{"code":"validation_error","message":"duration_s must be between 5 and 300"}}
```

The `error.code` value is a stable code; `error.message` explains the failure. Common
statuses are `400`, `401`, `403`, `404`, `405`, `409`, `413`, `422`, and `500`.
There are no `/docs` or `/openapi.json` endpoints.

## About and application settings

### `GET /api/about`

Returns the app and Python versions, detected Windows version and machine
model, data-folder path, history database size and first retained sample date,
and credits/course text from `[about]` in `config/default_config.toml`. The
live import check uses the same project scanner as `tools/check_imports.py`;
`import_check.imports_found` is an empty list when no third-party imports are
found.

### `GET /api/settings`

Returns the current settings, settings-file and data-folder paths, discovered
sensors, and history values that need an app restart. The UI settings file is
`data/settings.json`, not `config/default_config.toml`.

### `POST /api/settings`

Requires `X-Overpine-Token`. A normal update must include all fields:
`history_enabled` (boolean), `retention_days` (integer `1..3650`), `max_mb`
(integer `1..10240`), `recorded_sensors` (up to 64 valid sensor IDs),
`refresh_interval_seconds` (integer `1..60`), `theme` (`dark` or `light`), and
`online_device_fact_lookup` (boolean). Unknown, missing, or invalid fields are
rejected with `422`. The settings file is atomically saved inside the app data
folder. History recording, retention, size limit, and sensor selection take
effect after an app restart; refresh interval, theme, and online lookup apply
immediately.

Reset with the token-protected body `{"reset":true}`. This removes
`data/settings.json` and returns to config defaults.

## Persistent history

### `GET /api/history/sensors`

Returns sensors that currently have persisted samples:

```json
{
  "sensors": [{"id":"cpu.load","name":"CPU load","unit":"%","category":"cpu"}],
  "status": {
    "enabled": true,
    "available": true,
    "warning": null,
    "database_size_bytes": 4096,
    "retention_days": 7,
    "max_mb": 100,
    "pending_samples": 0
  }
}
```

### `GET /api/history?sensor=...&from=...&to=...&resolution=auto`

Returns sensor history for finite Unix timestamps (`from` and `to`, in
seconds). `from` must be earlier than `to`. `resolution` is `auto`, `raw`,
`minute`, or `5-minute`. `auto` selects raw readings for ranges up to two
minutes, one-minute averages for ranges up to ten minutes, and five-minute
averages for older ranges. Longer ranges include any raw and one-minute points
that fall inside the requested interval. Each sample has `ts`, `value`, `min`,
`max`, `state`, and `source` (`raw`, `minute`, or `five_minute`). Values remain
`null` whenever no reading was available. The response includes the same
storage `status` object as `/api/history/sensors`.

### `POST /api/history/clear`

Requires `X-Overpine-Token`. Clears raw and both aggregate tables and returns
`{"cleared":true,"status":...}`.

## Endpoints

### `GET /api/v1/health`

Returns application health and best-effort metrics for the current process.

```json
{
  "status": "ok",
  "app": "OVERPINE Project Software",
  "version": "0.5.0",
  "is_admin": false,
  "uptime_seconds": 123.4,
  "self": {"cpu_percent": null, "ram_mb": 20.0}
}
```

Unavailable metric values are `null`.

### `POST /api/v1/heartbeat`

Requires `X-Overpine-Token`. The desktop frontend sends this request every two
seconds; the launcher shuts down the local server after ten seconds without a
heartbeat. Returns `{"status":"ok"}`. No request body is required.

### `GET /api/v1/system`

Returns local device details and current system readings. The object contains
`device_name`, `device_model`, `manufacturer`, `product_code`, `bios_version`,
`operating_system`, `os_version`, `architecture`, `processor`,
`physical_cores`, `logical_processors`, `graphics_adapters`,
`cpu_usage_percent`, `cpu_frequency_mhz`, `memory_total_gib`,
`memory_available_gib`, `memory_used_percent`, `disk_read_bytes_per_sec`,
`disk_write_bytes_per_sec`, `network_receive_bytes_per_sec`,
`network_send_bytes_per_sec`, `gpu_usage_percent`, `sensor_sampled_at`,
`hardware_sections`, `hardware_inventory_status`, `system_drive`, and
`battery`. Readings that Windows does not expose are `null` or represented as
unavailable in the associated hardware-section rows.

### `GET /api/v1/device-note`

Returns a device note object:

```json
{
  "fact": "A sourced device fact, or null",
  "source": "Source label, or null",
  "source_url": "Source URL, or null",
  "message": null,
  "motto": "Original device motto",
  "motto_attribution": "Attribution"
}
```

Wikipedia facts are disabled by default. When disabled, `fact`, `source`, and
`source_url` are `null` and `message` explains that lookups are off. No external
request is made.

### `GET /api/v1/settings/online-lookups`

Returns `{"enabled":false}` or `{"enabled":true}` for this server session.

### `POST /api/v1/settings/online-lookups`

Requires `X-Overpine-Token` and the JSON body `{"enabled":true}` or
`{"enabled":false}`. Returns the resulting setting. Enabling this setting
explicitly permits the optional online device-fact lookup. It is the only
feature that can use the network and is off by default.

### `GET /api/v1/io`

Returns rolling disk and network throughput samples in bytes per second:

```json
{
  "points": [
    {
      "ts": 1700000000.0,
      "disk_read_bps": 100.0,
      "disk_write_bps": 200.0,
      "net_down_bps": null,
      "net_up_bps": null
    }
  ],
  "error": null
}
```

The first request may return an empty `points` array while rate counters collect
their first samples. Unavailable rates are `null`.

### `GET /api/v1/sensors`

Returns `{"sensors":[...]}`. Optional query parameters:

- `category`: exact sensor category.
- `state`: one of `AVAILABLE`, `UNAVAILABLE`, `UNSUPPORTED`,
  `PERMISSION_RESTRICTED`, `TEMPORARILY_DISCONNECTED`, `ERROR`, or
  `NOT_INITIALIZED`.

Each sensor has `id`, `name`, `category`, `unit`, `source`, `reliability`,
`state`, `value`, `last_update`, `min`, `max`, `avg`, and `error_message`.
Unavailable readings remain `null`.

### `GET /api/v1/sensors/{sensor_id}`

Returns one sensor object in the same shape as the list item. Unknown sensors
return `404`; malformed sensor IDs return `422`.

### `GET /api/v1/sensors/{sensor_id}/history`

Returns `{"sensor_id":"...","history":[...]}`. The optional `limit` query
parameter defaults to `3600` and must be between `1` and `3600`. Each history
sample contains `timestamp` and `value`.

### `GET /api/v1/monitoring/status`

Returns scheduler state with `state`, `running`, `paused`,
`session_started_at`, `session_duration_s`, `providers`, and `sensor_counts`.
Provider and state-count details reflect the current runtime discovery.

### `POST /api/v1/monitoring/pause`

Requires `X-Overpine-Token`. Returns the monitoring status object after polling
is paused. No request body is required.

### `POST /api/v1/monitoring/resume`

Requires `X-Overpine-Token`. Returns the monitoring status object after polling
resumes. No request body is required.

### `GET /api/v1/stress/token`

Returns `{"token":"..."}` to loopback clients. The token is unique to the
application session.

### `POST /api/v1/stress/start`

Requires `X-Overpine-Token` and a JSON object:

```json
{
  "targets": ["cpu"],
  "duration_s": 30,
  "intensity_percent": 50
}
```

`targets` is a list of `cpu`, `memory`, `disk`, `network`, `gpu`, or `all`;
`all` cannot be combined with individual targets. `duration_s` must be
`5..300`; `intensity_percent` must be `10..100`. Returns the stress status
object with `200`; conflicting runs return `409`, and invalid input returns
`422`.

### `POST /api/v1/stress/stop`

Requires `X-Overpine-Token`. Stops active workloads and returns the stress
status object. No request body is required.

### `GET /api/v1/stress/status`

Returns the current stress status object with `state`, `targets`,
`target_states`, `duration_s`, `intensity_percent`, `elapsed`, `remaining`,
`abort_reason`, `temperature_status`, `cpu_temperature_c`, and `live_metrics`.

### `GET /api/v1/stress/results`

Returns `{"result":...}`. `result` is `null` until a stress run has completed;
otherwise it contains run metadata, metric summaries, and per-target summaries.

### `POST /api/v1/admin/restart-as-admin`

Requires `X-Overpine-Token`. Schedules the Windows elevation request and
returns HTTP `202` with `{"status":"restart_requested"}`. Returns `409` if the
application is already running as administrator.
