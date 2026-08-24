# CLAUDE.md — SPRT/NTC calibration logging + bath control

Project context for anyone (human or Claude Code) picking this repo up.
Start with **`docs/HANDOVER.md`**; this file is the short version plus the rules.

## What this is
Two serial instruments are logged in parallel for NTC calibration against an
SPRT: the **Isotech MicroK** bridge (SPRT reference) and the **SchwaRTech/AWI
temperature head** (NTCs). `calibration_auto.py` additionally drives the
**Isotech Libra 785** bath through a plateau schedule. The calibration fit
itself happens later in R (`../CalibrationChains`), not here.

## Hardware facts that cost time to rediscover
- The **Libra 785 carries two controllers on one serial line**: the Eurotherm
  **3504** (the actual controller) speaks **EI-Bisynch, 7E1, address 1**; the
  **over-temperature limiter** speaks **Modbus RTU, 8N1, slave 2**. A Modbus
  scan finds only the limiter — the bath is *not* reachable that way. Drive it
  with `bisynch.py`. Read-once values from the unit: setpoint limits `HS`/`LS`
  ≈ +125 / −90 °C.
- The **head keeps its node array in its own memory**, and **every command
  restarts it**. So `NODES …` is issued exactly once per run and never again.
- The bath slews only ~2 °C/min and overshoots ~2 °C at full power; the
  approach ramp (`ramp_c_per_min: 1`) is the current mitigation.

## Rules for changes
- **Never change the output file formats without updating `docs/DATA_FORMATS.md`.**
  The R pipeline reads them positionally; the `Group1;` tag and the 4-field
  layout are kept for backward compatibility even though there is only one
  group left. New information goes in *appended* fields (as `TOFFMS` did).
- **Display conversions never touch the files.** `sprt.py` and `ntc.py` exist so
  the operator sees temperatures on screen; the logs stay raw (ratio / counts).
  Neither is a per-sensor calibration — that stays in R.
- **`calibration_log.py` owns the logging machinery**; `calibration_auto.py`
  imports it and adds only bath orchestration. Keep it that way, so both tools
  keep writing identical files.
- **Serial writes move real hardware.** `--read`, `--scan`, `--identify`,
  `--monitor` and `--dry-run` are safe; `--write`/`--set` command the bath. Try
  a read-only path first, and never leave a run without the over-temp limiter.
- **Tests must stay hardware-free**: `python3 tests/test_bath.py`,
  `python3 tests/test_bisynch.py` (fake serial + simulator). Run both before
  committing; add cases when touching gate, config parsing or row parsing.
- One program per serial port at a time.

## Where things are documented
| Question | File |
|---|---|
| How do I take this over / run it? | `docs/HANDOVER.md` |
| How do I operate it, what do the config keys mean? | `README.md` |
| What exactly is in the output files (for R)? | `docs/DATA_FORMATS.md` |
| How do I talk to the bath, which commands exist? | `docs/HANDOVER.md` §6, `bisynch.py` header |
| How would we fix the overshoot properly? | `docs/PID_TUNING.md` |
| What is still open? | `docs/TODO.md` |
