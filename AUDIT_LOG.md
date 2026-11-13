# OVERPINE Project Software: Audit Log

Last updated: 2026-11-13

## How to read this log

Every status below is tied to evidence: a terminal output, a log file, a screenshot or page text, a README statement, or a test name.

- **Verified**: a real output from the author's laptop shows it.
- **Reported only**: a README or an AI assistant says so, but no output backs it yet.
- **Unknown**: nothing seen since the last check.

Limits of this audit:
- The auditor (Claude) runs on Linux and cannot run the Windows APIs. It has read the project's GitHub repository (commit `2ed28ff`, 2026-11-13) and ran the new Diagnostics tests there: 7 of 7 passed. In the same Linux run 4 Windows-only tests fail, and they fail the same way on the untouched repository.
- Everything about real Windows behavior comes from output pasted by the author.
- Items marked "Unknown" have not been re-checked since the date given.

## 1. Honest status in one paragraph

The project is a working prototype with a standard-library backend and a browser dashboard. It has a stress test, a sensor engine, persistent SQLite history with a size cap, and About, Settings, Help and Diagnostics pages. The author's laptop passed 141 unit tests on 2026-11-13, and `tools/check_imports.py` reports no third-party imports. The Windows readings use the project's own `ctypes` code. It is not finished or fully verified. The hardware inventory service runs Windows PowerShell and its live query times out repeatedly. The CPU throttling estimate stays unavailable. Exact CPU core temperature, package power and fan RPM are not available on this laptop. The desktop window and the `.exe` are paused. Widgets, custom themes, reports and notifications are not started.

## 2. Timeline

| Date (local) | What happened | Evidence |
|---|---|---|
| 2026-10-30 | Phase 1 proposal, Phase 2 skeleton, Phase 3 dashboard and hardware inventory. Two slow queries seen in the log (inventory 12 s, GPU counter 2.5 s). | Chat, `logs/overpine.log` lines. |
| 2026-11-01 | **Lecturer's rule received: no third-party Python libraries at all, psutil included.** Plan changed to a standard-library-only rewrite. | Author's message. |
| 2026-11-01 | Laptop probes: one thermal zone (`\_tz.thm0`, value 353), Energy Meter RAPL instances (pkg, pp0, pp1 non-zero; dram and total read 0), no `Win32_Fan` data. | PowerShell output pasted by the author. |
| 2026-11-01/02 | Standard-library web server done. 70, then 74 tests OK. `check_imports`: none found. `selftest` printed live Windows values. | Terminal output. |
| 2026-11-09 | 80 tests OK. `diagnose` showed suspicious values (all cores exactly 100.0%, network/disk unavailable in the table, CPU frequency constant 1201.0 MHz). | Terminal output. |
| 2026-11-10 | After sensor Step 3: `diagnose` summary 38 AVAILABLE, 7 UNAVAILABLE, 2 UNSUPPORTED, 498 NOT_INITIALIZED. CPU frequency source is now the PDH counter. | Terminal output. |
| 2026-11-11 | History page works (screenshot with battery chart). Heartbeat test was flaky (timing assertion); fixed. 113 tests OK. Measured 21,000 history rows per minute and a 76 MB database. | Terminal output, screenshot, Copilot measurement. |
| 2026-11-12 | History write volume cut to about 164 rows per minute (database about 7 MB). Tests changed to use temporary data folders only. 127 tests OK. | Copilot report, terminal output, file size and time unchanged before/after. |
| 2026-11-13 | About, Settings, Help pages done. Settings sensor list cut from about 480 to 17 entries. Help wording fixed. 134 tests OK. | Page text pasted by the author, terminal output. |
| 2026-11-13 | Project pushed to GitHub (`Raflisy/overpine`, public). The first push contained the old FastAPI/psutil copy; it was replaced by a force-push with the standard-library version. Local data files are not tracked. | Push output, repository read by the auditor. |
| 2026-11-13 | Diagnostics page and API added. 141 tests OK. The page showed 13 AVAILABLE, 2 UNAVAILABLE, 2 UNSUPPORTED among 17 counted sensors and 470 detail sensors. The log view exposed the issues in section 5. | Terminal output, page text. |
| 2026-11-13 | Aborted browser connections were being logged as errors; fix pushed (commit `2ed28ff`, change present in all 3 places). | Repository read by the auditor. |

## 3. Decisions

| ID | Decision | Reason |
|---|---|---|
| D1 | Python standard library only. No third-party Python packages. | Lecturer's rule. |
| D2 | LibreHardwareMonitor dropped, including its web server. | It is a third-party library. |
| D3 | No kernel driver and no CPU register or port access. | Needs a signed driver; unsafe; out of scope. Exact core temperature and package power are not promised. |
| D4 | Read-only monitoring. Never change voltage, clocks, fans, power limits or firmware. | Safety. |
| D5 | Server on `127.0.0.1` only, with Host and Origin checks and a per-session token for state-changing requests. | Security design. |
| D6 | Missing data stays null with a state. Nothing is invented or simulated. | Project rule. |
| D7 | Tests use `unittest` with temporary data folders. Charts use the project's own canvas code. | No third-party libraries; tests must not touch real data. |
| D8 | Desktop window (Edge app mode) and `.exe` build paused. | PyInstaller is third-party; waiting for the lecturer. |
| D9 | Online device-fact lookup is off by default. | Privacy. |
| D10 | History stores only a short list of system-wide sensors, never per-process detail rows. Retention 7 days, hard cap 100 MB. | The first version wrote about 21,000 rows per minute. |

## 4. Evidence check of what the README claims

| Claim | Evidence | Status |
|---|---|---|
| No third-party Python imports | `tools/check_imports.py` "None found" on 2026-11-13 (author) and on the repository (auditor) | Verified |
| 141 unit tests pass | Author's terminal, 2026-11-13 | Verified |
| Tests never touch the real data folder | A guard test exists; `history.db` size and time were unchanged before and after a full run (2026-11-12) | Verified |
| History stores real values with states, capped in size | Page screenshot; measured rows and size on 2026-11-12 | Verified for the short measurement. Long-run growth Unknown (estimate 4.5 days to the cap from a 3-minute run) |
| CPU, memory, disk, network read with own `ctypes` code | `selftest` 2026-11-02; `diagnose` 2026-11-10 | Partly verified. No reported side-by-side comparison with Task Manager |
| CPU frequency from the PDH counter | `diagnose` reports the source on 2026-11-10 | Verified (value accuracy not compared with Task Manager) |
| Stress test with safety limits | Unit tests with mocked hardware; abort messages in test output | Tests only. No real run reported |
| Online lookup off by default | Test passes | Verified by test |
| Diagnostics page and logs | Page text pasted on 2026-11-13 | Verified |
| "External Windows programs are disabled by default" (old README) | `allow_external_programs` guards only the WMI thermal provider; the inventory service starts PowerShell regardless | **Incorrect as written. Corrected in the new README** |
| Fan RPM | No data on this laptop; reported UNSUPPORTED | Verified |
| Desktop window / `.exe` | Paused | Not available |

## 5. Open issues

| ID | Issue | Severity | Next action |
|---|---|---|---|
| P1 | **PowerShell use.** `backend/services/hardware_inventory.py` starts `powershell.exe` (`Get-CimInstance`) for the device inventory (45 s timeout) and for a live GPU/battery query about every 10 s (10 s timeout). The setting `allow_external_programs` does not control it. The log shows hundreds of "Live Windows sensor query timed out after 10 seconds" warnings on 2026-11-12 and 2026-11-13, and bursts of slow `/api/v1/system` requests. | High | Ask the lecturer (question 2). If not allowed, replace with native `ctypes`/registry code; the native battery and GPU providers already cover most live values. |
| P2 | CPU throttling estimate (`cpu.throttling.estimated`) stays UNAVAILABLE ("two meaningful samples at least 1 s apart") after more than six minutes of runtime. Cause not found. | Medium | Check the GetSystemTimes sampling interval. |
| P3 | `windows-counter` provider times out ("Provider call timed out after 11 seconds") about every four minutes. Cause not confirmed; it may wait behind the PowerShell lock. | Medium | Re-check after P1. |
| P4 | Aborted browser connections were logged as ERROR with tracebacks (about 100 entries on 2026-11-13). | Fixed in `2ed28ff` | Confirm the log is quieter after a run. |
| P5 | One-off errors on 2026-11-12 13:46: disk performance counter failure (PDH status 0xC0000BC6), adapter list failure, "The media is write protected". Not seen again in the log. | Low | Watch. |
| P6 | Dashboard requests `cpu.package.temperature`, which does not exist on this laptop; the console shows a 404 each time. | Low | Skip the request when the sensor is not in the list. |
| P7 | About page credits and course/university are empty (`[about]` in `config/default_config.toml`). | Low | Author fills them in. |
| P8 | The repository is public. | Author's choice | Make it private if wanted. |
| P9 | Six cleanup bugs from the 2026-11-02 self-test (duplicate network adapters, process CPU above 100%, and others) have no reported outcome. | Medium | Re-run the self-test and confirm each. |
| P10 | CPU % and CPU speed have not been compared with Task Manager. | Medium | One-minute side-by-side check. |
| P11 | Drive C: was almost full on 2026-11-02 (about 2.1 GB). Current free space Unknown. | Medium (author's machine) | Free space before long tests. |
| P12 | The stress test refuses to start below 30% battery when unplugged. | Info | Plug in before testing. |
| P13 | Per-process counters are in the sensor registry; ended processes appear as TEMPORARILY_DISCONNECTED and are not removed. | Low | Add removal later. |
| P14 | Widgets, custom themes, reports and notifications are not started. | Planned | See roadmap. |

## 6. Questions for the lecturer

1. Is the standard library allowed, including `ctypes`, `http.server` and `sqlite3`?
2. Are built-in Windows tools (PowerShell, `netsh`) allowed when called from Python? **This now decides how the hardware inventory is built (issue P1).**
3. Is plain JavaScript in the browser fine, with charts drawn on a canvas by hand?
4. Is PyInstaller allowed to package an `.exe`, or should the project run from source only?

Status: not sent yet (as of 2026-11-13).

## 7. Next steps

1. Send the four questions to the lecturer.
2. After the answer: remove or replace the PowerShell service (P1), then re-check P2 and P3.
3. Compare CPU %, CPU speed, memory and network with Task Manager (P10).
4. Fill in `[about]`; skip the `cpu.package.temperature` request (P6).
5. If PyInstaller is allowed: desktop window and `.exe` release.
6. Optional later: reports, widgets, custom themes, notifications.

## 8. Roadmap status

| Phase | Item | Status |
|---|---|---|
| 1 | Architecture proposal | Done |
| 2 | Skeleton | Done |
| 3 | Hardware identification and dashboard | Implemented; uses PowerShell (P1) |
| 4 | Sensor engine and diagnostics | Implemented; open issues P2, P3, P10 |
| 5 | API | REST routes documented in `docs/api.md`; no live push |
| 6-7 | Frontend and charts | Implemented (own canvas charts) |
| 8 | Widget customization | Not started |
| 9 | Custom themes | Light/dark done; custom colors not started |
| 10 | Diagnostics and notifications | Diagnostics page done; notifications not started |
| 11 | History and export | Done (SQLite, CSV export, size cap) |
| 12 | Testing and optimization | 141 unit tests; resource report not done |
| 13 | Documentation and packaging | README and API docs updated; packaging paused |
