# SPRT–NTC Calibration Logging

Parallel data logging for thermistor (NTC) calibration against an SPRT reference.
Two serial instruments are read **simultaneously** by a small command-line logger:

- **Isotech MicroK** precision resistance bridge — reads the **SPRT** (reference thermometer).
- **SchwaRTech/AWI Temperature head** — reads the **NTCs** (and optional test channels).

Both streams are timestamped (`datetime`) on every line, so the two data files
can be matched up afterwards purely by their wall-clock timestamps.

Two ways to run:

- **`calibration_log.py`** — *legacy / passive*. Logs both instruments; the bath
  temperature is set **manually**. It contains the whole logging machinery (both
  serial workers, the token-accurate NTC readout, the meta file) — `calibration_auto.py`
  imports it and only adds the bath control on top, so both tools write byte-identical
  data files.
- **`calibration_auto.py`** — *automated*. Additionally drives the **Isotech Libra
  785** bath through a list of temperature plateaus (via `bisynch.py`, EI-Bisynch
  over RS232) while logging continuously, ends each plateau when the SPRT reference
  stops drifting, and shows the live **SPRT temperature** next to the bath
  setpoint/PV so you can see at a glance where things stand.

---

## Requirements

- **macOS** (serial ports are addressed as `/dev/cu.*`)
- **Python 3**
- **[pyserial](https://pyserial.readthedocs.io/)**

```bash
python3 -m pip install -r requirements.txt
```

> If `import serial` fails, make sure it is **pyserial** (`python3 -m pip install pyserial`)
> and not the unrelated PyPI package also called `serial`.

## Hardware / wiring

| Instrument                    | Typical USB adapter      | Baud  |
|-------------------------------|--------------------------|-------|
| MicroK bridge (SPRT)          | FTDI FT232R (single)     | 9600  |
| SchwaRTech/AWI Temperature head | FTDI FT2232H (dual, ch0) | 19200 |

The exact `/dev/cu.usbserial-*` names can change between sessions — the script
lets you **pick the port interactively at runtime** (see below). If you are unsure
which device is on which port, `tools/port_detect.py` lists them and can probe them
read-only (`--list`, `--map config/param_combined.txt`, `--probe`) — run it with the
calibration stopped, one program per port.

---

## Usage

Edit **`config/param_combined.txt`** first (see format below), then run:

```bash
python3 calibration_log.py                 # uses config/param_combined.txt
python3 calibration_log.py --param x.txt    # different config file
python3 calibration_log.py --exp Testlauf   # override the experiment name
```

At startup the script:
1. lists the detected serial ports and asks you to pick which one is the MicroK
   and which is the SchwaRTech/AWI Temperature head (press **Enter** to accept the pre-selection
   from the config);
2. asks for a **free-text description of the calibration** (until Enter) — this is
   stored in the `_meta.txt` file;
3. starts both logging threads.

**Stop with `Ctrl-C`** — both threads shut down cleanly and close their ports.
Robust for long, unattended runs (e.g. inside `tmux`/`nohup` over SSH).

### Live output

Both raw values **and** the converted temperatures are shown on screen (the
files keep the raw values only — the display conversion never touches them):

```
[MicroK] Port open : /dev/cu.usbserial-A922BJHF
[TempHead] Head is awake.
[MicroK] #1 Ch2: 0.234568   ->  -19.2540 C
[TempHead] 1: 5812827 | … || 4540193 | …   ->  N94=-46.661 N96=-39.454
...
```

The MicroK line adds the **SPRT temperature** (`sprt.py`); the head line adds the
**raw NTC temperature per node for every configured NTC channel** — `NTC1`, `NTC2`
and `TestSB` (which is wired as a second NTC) — via `ntc.py`. The NTC conversion
uses the universal mean lab-S4 calibration (see below) and is for orientation only
— the file contents are unchanged, so the R calibration pipeline is unaffected.

A node whose raw counts exceed `10_000_000` (open input) is reported as
`!! Nodes not connected: N91` instead of a temperature.

Output files can be **copied at any time while logging** — every line is
`flush()`ed to disk immediately, so a copy always contains all completed lines.

---

## Configuration — `config/param_combined.txt`

Minimal `key: value` format (order does not matter, `#` starts a comment):

```
experiment: Snowmelt_Retest5Sensors     # goes into the output file names

microk_channels: 1;2                     # Reference ; SPRT1 [ ; SPRT2 ]
microk_port: usbserial-A922BJHF          # optional port pre-selection

ntc_readout: NTC1;NTC2;TestSB            # from: TempADC, NTC1, NTC2, TestSB, TestN, GND, PRESSURE
ntc_groups: 5;60                         # legacy field, no longer chunks nodes (see below)
ntc_nodes: 90;91;92;93;94                # sensor node IDs
ntc_port: usbserial-FT3GCNKB0            # optional port pre-selection
```

| Key               | Meaning                                                        |
|-------------------|----------------------------------------------------------------|
| `experiment`      | Experiment name, used in output file names                     |
| `microk_channels` | Bridge channels: `Reference; SPRT1` (add `;SPRT2` for two SPRTs) |
| `microk_port`     | Optional MicroK port hint (pre-selection only)                 |
| `ntc_readout`     | Which sensor channels to log                                   |
| `ntc_groups`      | Legacy — parsed for the meta file only; nodes are no longer chunked |
| `ntc_nodes`       | Sensor node IDs                                                |
| `ntc_port`        | Optional Temperature-head port hint (pre-selection only)       |

---

## Output — three files per run, in `./data/Output/` (four with `calibration_auto.py`)

```
<experiment>_<YYYYMMDD-HHMMSS>_microk.txt   # MicroK / SPRT data
<experiment>_<YYYYMMDD-HHMMSS>_ntc.txt      # NTC logger data
<experiment>_<YYYYMMDD-HHMMSS>_meta.txt     # resolved settings + verbatim copy of the param file
```

All three share the same timestamp, so one run = one matching set. The `_meta.txt`
records the actually-chosen ports and all parameters, so the measurement settings
are always recoverable later.

### How the NTC readout works

The SchwaRTech/AWI temperature head is a **multiplexed** system: each physical
sensor module is a **node** (`ntc_nodes`, e.g. `90;91;92;93;94`). You tell the
head which nodes to read with a single `NODES 90 91 92 …` command; it then streams
one reading line per sweep containing exactly those nodes, and it **keeps that node
array in its own memory** — across sweeps and even across restarts.

Because of that, the logger reads **every node as one continuous array**: it issues
`NODES` **once** at startup and then just streams and logs, never re-issuing the
command. (Earlier versions chunked the nodes into groups and round-robined between
them, re-issuing `NODES` for each — but every command restarts the head, so with a
short array that produced nothing but repeated header lines. Reading all nodes as
one array removes the problem entirely.)

`ntc_readout` picks which channels each node outputs (`NTC1`, `NTC2`, `TestSB`, …);
`DateTime`/`TempADC` are per-line, the rest are per-node. `ntc_groups` is a **legacy
field**: it is still parsed (and echoed into the meta file) but no longer chunks the
nodes — all nodes always go into the one array.

**In the `_ntc.txt` file** the header is written **once** at the start, followed by
a continuous stream of data rows (all tagged `Group1;` for backward compatibility):

```
Group1; SecondsElapsed; DateTimePC; N94_NTC1 | N94_NTC2 | N94_TestSB || N96_NTC1 | N96_NTC2 | N96_TestSB
Group1; 9.19;  2026-07-02 15:59:58.58; 5812827 | 5732217 | 5648185 || 5540969 | 5471459 | 5400559
Group1; 13.77; 2026-07-02 16:00:03.15; 5310211 | 5253038 | 5192965 || 5113788 | 5066173 | 5013578
…
```

- Within a data row, `|` separates channels **within** a node and `||` separates
  **nodes**. Every row fills all node columns — no blanks.
- The header appears only once (not per cycle), so on a long run it scrolls out of a
  tail-read window; readers fall back to the known column layout from the config.
  `New Node Array:` / echo lines are recognised structurally and filtered out.
- Rows that arrive incomplete (Ctrl-C, or the head stalling mid-line) are dropped
  rather than written, so every row in the file is full width.
- If **no** data row arrives for 25 s (`RESYNC_AFTER` in `calibration_log.py`), the
  logger re-sends `NODES` **once** to nudge a genuinely stuck head — never more
  often, because every command restarts the head's sweep.
- One array = one clean wide table, directly usable.

**Per-value timestamps (`TOFFMS`).** The head measures its sensors **sequentially**
(number, `|`, next number, …), so a many-node line takes tens of seconds to over a
minute to stream — and the bath oscillates ~10 mK on a ~2 min limit cycle. Tagging
all values of a line with one timestamp would alias that oscillation into several
mK of per-sensor error. The logger therefore reads the head **byte-by-byte** and
records each value's true arrival time, appending a 5th `;`-field to each data row:

```
Group1; 18.33; 2026-07-06 19:09:44.623; 861775 | … || … ; TOFFMS=0|763|1526|2290|…
```

`TOFFMS` holds one integer per value: milliseconds from the **first** value (whose
time is the row's `DateTimePC`), in left-to-right order. So value *k*'s absolute
time is `DateTimePC + TOFFMS[k] ms`. Old 4-field readers ignore the field (the
`counts` block is unchanged); the R pipeline uses it to pair each count with the
SPRT **interpolated to that exact instant**, so the common-mode bath oscillation
cancels instead of aliasing (see `docs/DATA_FORMATS.md`).

---

## Automated bath-driven runs — `calibration_auto.py`

Same logging as the legacy tool, but it also steps the **Isotech Libra 785** bath
through a list of temperature plateaus.

**How this bath is actually wired (verified on the unit).** The Libra 785 has
**two controllers on one serial line**, addressed separately and answered by the
PC master one request at a time (never simultaneously — the master switches byte
framing per request, exactly as Isotech's *Cal NotePad* does):

| Controller | Role | Protocol | Framing | Address |
|---|---|---|---|---|
| **Eurotherm 3504** | main **CONTROLLER** — bath setpoint & PV | **EI-Bisynch** | **7E1** | **1** |
| Isotech "OVER TEMPERATURE" | safety limiter (read its temp only) | Modbus RTU | 8N1 | 2 |

So the bath is driven over **EI-Bisynch** (`bisynch.py`), *not* Modbus. Mnemonics
in use: `PV` (measured), `SL` (writable setpoint), `OP` (% output), `RR` (ramp
rate). `bath.py` (Modbus) still reads the over-temp limiter and remains available
for other Isotech units whose controller is configured for Modbus.
`calibration_auto.py` picks the path via the `bath_protocol` config key
(default `bisynch`).

```bash
python3 -m pip install -r requirements.txt      # now also installs minimalmodbus
python3 calibration_auto.py --dry-run           # connect + read the bath, don't move it
python3 calibration_auto.py                      # full automated run (scrolling log)
python3 calibration_auto.py --dashboard          # fixed in-place overview panel
```

`--dashboard` replaces the scrolling log with a single panel that refreshes in
place — plateau progress `i/N`, phase (settle/**gate**/dwell), bath PV/SP/OUT/ramp,
the SPRT temperature, the live drift/sd gate metrics, and a live NTC table for
**all** nodes. The loggers still write their files exactly as before; only the
console view changes.

### Quick bath-only usage — `bath.py` (no logging)

For just driving/reading the bath, `bath.py` is a self-contained CLI — no MicroK,
no NTC, no config file. All modes read PV/SP/OUT once first as a smoke test.

```bash
python3 bath.py                                  # read PV / setpoint / output once
python3 bath.py --monitor                         # keep reading it out (Ctrl-C to stop)
python3 bath.py --set -40                          # set setpoint and exit
python3 bath.py --wait -40 --minutes 20            # set, wait until stable, hold 20 min
python3 bath.py --plateaus "-40;-20;0;20" --minutes 15   # run a ramp/plateau schedule
python3 bath.py --plateaus "0;25;50" --ramp-rate 5       # limited approach ramp (5 C/min)
```

Common flags: `--encoding float|int1|int2|int3` (value scaling, default `int1`),
`--slave N`, `--tol`, `--window`, `--timeout`. If PV/SP read back wrong, the
encoding or baud/parity (in `bath.py`) is off — see the header notes.

> **Note:** on the Libra 785, `bath.py` (Modbus) only reaches the **over-temp
> limiter** (slave 2). To drive the bath itself use the **EI-Bisynch** path —
> `bisynch.py` for one-off commands, or `calibration_auto.py` with
> `bath_protocol: bisynch` (default) for a full run.

**Per plateau (`calibration_auto.py`):** (optional ramp →) set setpoint → coarse
settle (bath PV inside `stability_tol` for `stability_window` minutes) → **soak
until the plateau gate accepts** → next. A fourth output file
`<experiment>_<time>_plateaus.txt` records each plateau's setpoint, timestamps and
gate metrics so the measurement window can be sliced out afterwards.

**The plateau gate (`gate_mode`).** A plateau is "done" not on a fixed clock but
when the **SPRT reference stops moving** (`gate_mode: drift`, the default): accept
the window once, over the trailing `gate_window_min` minutes, the fitted drift is
`< gate_drift_mk` **and** the scatter is `< gate_sd_mk`. Slow creep (a few mK over
half an hour) does no harm to the fit; a live dT/dt does — and the bath's own PV
(10 mK resolution) cannot even see a 3 mK drift, so the gate reads the MicroK SPRT
(sub-mK). `plateau_minutes` becomes the **minimum soak** before the gate may
accept (long for the cold end, which creeps for hours); a hard cap
(`plateau_minutes + gate_max_extra_min`) ends and **flags** any plateau whose
reference never settles. `gate_mode: fixed` keeps the old fixed-time dwell. The
accepted `[t_dwell_start, t_dwell_end]` in the plateau file **is** the trailing
average window; the plateau-file header lists the exact columns (drift mode adds
`gate_ok; drift_mK; sd_mK; n`).

The live status line shows bath PV/SP, the **SPRT temperature**
(via `sprt.py`) **and** the **raw NTC temperature per node for each NTC channel**
(NTC1/NTC2/TestSB, via `ntc.py`) so you
see the whole picture at a plateau. Both conversions are **display only** — SPRT:
the 2-point ratio calibration; NTC: the **universal mean lab-S4 calibration** (a
4-param Steinhart–Hart curve averaged over the 62 healthy GRIP sensors, `ntc.py`
`NTC_MEAN_COEF`), which tracks a healthy sensor to ±0.03–0.05 °C and replaces the
old β=3380 curve that read ~0.7 °C (0 °C) to ~3.6 °C (−40 °C) too warm. It is not a
per-sensor fit — the authoritative per-sensor calibration stays in the R pipeline.

⚠️ **Verify once against the real controller** (see `bath.py` header): serial
baud/parity, Modbus slave address, and the **value encoding**
(`bath_encoding: float` vs `int1/int2/int3`). Run `--dry-run` first — if PV/SP
read back as sensible numbers, the settings are right.

Everything lives in the **single** `config/param_combined.txt`, grouped into sections:
**(1) hardware** — the three serial ports at the very top (they change when a
USB-serial adapter is re-plugged); **(2) experiment**; **(3) bath controller**;
**(4) bath schedule & tuning**. Bath-automation keys (ignored by the legacy tool):

| Key                 | Meaning                                                          |
|---------------------|------------------------------------------------------------------|
| `bath_port`         | Bath-controller port hint (pre-selection)                        |
| `bath_protocol`     | `bisynch` (the 3504) \| `modbus` (Series-2000 units)             |
| `bath_address`      | Bisynch GID/UID address (the 3504 = `1`) / Modbus slave address  |
| `bath_encoding`     | **Modbus only:** `float`\|`int1`\|`int2`\|`int3` register scaling |
| `plateaus`          | 1–100 setpoints in °C, in run order (`;`-separated)             |
| `plateau_minutes`   | drift mode: **min soak** before the gate; fixed mode: exact dwell. one → all, or per plateau |
| `ramp_c_per_min`    | Approach ramp; empty → max speed; one → all, or per plateau      |
| `stability_tol`             | °C band around the setpoint counting as "arrived" (coarse)|
| `stability_window`          | minutes PV must stay in band before soaking              |
| `stability_timeout_per_10k` | max wait for arrival scales with step: minutes per 10 K  |
| `stability_timeout_min`     | floor (minutes) for small/zero steps                     |
| `gate_mode`         | `drift` (SPRT dT/dt gate, default) \| `fixed` (fixed-time dwell)  |
| `gate_drift_mk`     | accept when \|drift over the window\| < this (mK), default 3      |
| `gate_sd_mk`        | AND scatter about the fit line < this (mK), default 8            |
| `gate_window_min`   | trailing window for drift+sd **and** the average (min), default 30 |
| `gate_max_extra_min`| soak cap = `plateau_minutes` + this (min), default 120           |
| `gate_poll_s`       | how often the gate re-checks the SPRT log (s), default 30        |
| `gate_channel`      | SPRT channel to gate on; default = first configured SPRT channel |

If the coarse settle times out (step-scaled), the run **soaks on the SPRT gate
anyway**; if the gate never passes within the soak cap, it measures the trailing
window and **flags** the plateau. It never blocks the whole schedule.

A ready-to-run **24 h schedule** is provided as **`config/param_24h.txt`** — an 11-point
down-sweep with brackets (+5 … −35 in ~4 °C steps, 180 min soak at −35) plus 4
up-anchors (−25, −15, −5, +3) that re-visit interior temperatures later in time.
The up-anchors make time orthogonal to temperature (common-mode drift is
~+1.5 mK/day), which keeps the fitted coefficients from bending:

```bash
python3 calibration_auto.py --param config/param_24h.txt --dashboard
```

**Time estimate.** At start (and, in `--dashboard`, continuously) the run prints a
**probable** finish time and a **latest** finish time. "Latest" is a hard upper
bound = Σ(arrival timeout + worst-case measure) per plateau — in drift mode the
worst-case measure is the soak cap (`plateau_minutes + gate_max_extra_min`), so a
plateau always ends; "probable" assumes each settles in ramp + a typical soak
(≈ the empirical median).

### "No communication with the instrument"

The port opened but the controller did not reply. Sweep the serial settings
(read-only, changes nothing on the controller):

```bash
python3 bath.py --port /dev/cu.usbserial-XXXX --scan
```

It tries baud × parity × slave address and prints the first combination that
answers, e.g. `--baud 9600 --parity N --slave 1`. Once found, pass those flags to
the normal read-out.

If **only the over-temp limiter** answers Modbus (or nothing does), the main
controller is on **EI-Bisynch**, not Modbus — that is the normal Libra 785 case.
Find it read-only with the Bisynch scanner:

```bash
python3 bisynch.py --port /dev/cu.usbserial-XXXX --scan       # find framing + address
python3 bisynch.py --port ... --identify --addr 1             # map the mnemonics vs the panel
python3 bisynch.py --port ... --addr 1 --read PV              # read the measured temperature
python3 bisynch.py --port ... --addr 1 --write SL -15         # command a setpoint (changes the bath!)
```

`--scan` sweeps the Bisynch framings (7E1/7O1/8E1/8N1) × addresses and reports
what answers; on this bath it is **7E1, address 1**. `--identify` reads the common
mnemonics so you can match them against the 3504 front panel before writing.

---


## Repository layout

```
calibration_log.py      logger (MicroK + NTC head); also the library the auto tool imports
calibration_auto.py     automated run: same logging + bath plateau control
bisynch.py              EI-Bisynch control of the Eurotherm 3504 -- this drives the bath
bath.py                 Modbus RTU: the over-temp limiter here, the controller on Series-2000 units
gate.py                 SPRT dT/dt plateau gate (trailing drift/sd statistics)
sprt.py  ntc.py         ratio/counts -> temperature, live display only

config/                 parameter files -- param_combined.txt (default), param_24h.txt,
                        param_transient.txt
docs/                   HANDOVER.md (start here), DATA_FORMATS.md (output formats for R),
                        PID_TUNING.md (overshoot plan), TODO.md
tests/                  test_bath.py, test_bisynch.py -- offline, no hardware needed
tools/                  bath_sim.py (Modbus bath simulator), port_detect.py (which device
                        is on which port), requirements-sim.txt
data/Output/            measurement data written by a run (git-ignored)
```

Run the tools **from the repository root** -- the default parameter path
(`config/param_combined.txt`) and the output directory (`data/Output/`) are
relative to it. The test suites may be run from anywhere.

---

## Authors

- **Nora Hirsch** and **Kathrin Brocker** — original SPRT/NTC logging code.
- **The calibration team**, with **Claude Code** — bath automation, plateau
  drift-gating, token-accurate logging, and the calibration workflow.
