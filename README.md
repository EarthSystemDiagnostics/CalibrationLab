# SPRT–NTC Calibration Logging

Parallel data logging for thermistor (NTC) calibration against an SPRT reference.
Two serial instruments are read **simultaneously** by a small command-line logger:

- **Isotech MicroK** precision resistance bridge — reads the **SPRT** (reference thermometer).
- **SchwaRTech/AWI Temperature head** — reads the **NTCs** (and optional test channels).

Both streams are timestamped (`datetime`) on every line, so the two data files
can be matched up afterwards purely by their wall-clock timestamps.

Two ways to run:

- **`calibration_log.py`** — *legacy / passive*. Logs both instruments; the bath
  temperature is set **manually**. Unchanged.
- **`calibration_auto.py`** — *automated*. Additionally drives the **Isotech Libra
  785** bath through a list of temperature plateaus (via `bath.py`, Modbus over
  RS232) while logging continuously, and shows the live **SPRT temperature** next
  to the bath setpoint/PV so you can see at a glance where things stand.

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
lets you **pick the port interactively at runtime** (see below).

---

## Usage

Edit **`param_combined.txt`** first (see format below), then run:

```bash
python3 calibration_log.py                 # uses param_combined.txt
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
[TempHead] G1 1/63: 5812827 | … || 4540193 | …   ->  N94=-46.661 N96=-39.454
...
```

The MicroK line adds the **SPRT temperature** (`sprt.py`); the head line adds the
**raw NTC1 temperature per node** (`ntc.py`). Both use the nominal R-workflow
formulas and are for orientation only — the file contents are unchanged, so the R
calibration pipeline is unaffected.

Output files can be **copied at any time while logging** — every line is
`flush()`ed to disk immediately, so a copy always contains all completed lines.

---

## Configuration — `param_combined.txt`

Minimal `key: value` format (order does not matter, `#` starts a comment):

```
experiment: Snowmelt_Retest5Sensors     # goes into the output file names

microk_channels: 1;2                     # Reference ; SPRT1 [ ; SPRT2 ]
microk_port: usbserial-A922BJHF          # optional port pre-selection

ntc_readout: NTC1;NTC2;TestSB            # from: TempADC, NTC1, NTC2, TestSB, TestN, GND, PRESSURE
ntc_groups: 5;60                         # sensors per group ; measurement points per group
ntc_nodes: 90;91;92;93;94                # sensor node IDs
ntc_port: usbserial-FT3GCNKB0            # optional port pre-selection
```

| Key               | Meaning                                                        |
|-------------------|----------------------------------------------------------------|
| `experiment`      | Experiment name, used in output file names                     |
| `microk_channels` | Bridge channels: `Reference; SPRT1` (add `;SPRT2` for two SPRTs) |
| `microk_port`     | Optional MicroK port hint (pre-selection only)                 |
| `ntc_readout`     | Which sensor channels to log                                   |
| `ntc_groups`      | `sensors_per_group ; measurement_points_per_group`             |
| `ntc_nodes`       | Sensor node IDs                                                |
| `ntc_port`        | Optional Temperature-head port hint (pre-selection only)       |

---

## Output — three files per run, in `./Output/`

```
<experiment>_<YYYYMMDD-HHMMSS>_microk.txt   # MicroK / SPRT data
<experiment>_<YYYYMMDD-HHMMSS>_ntc.txt      # NTC logger data
<experiment>_<YYYYMMDD-HHMMSS>_meta.txt     # resolved settings + verbatim copy of the param file
```

All three share the same timestamp, so one run = one matching set. The `_meta.txt`
records the actually-chosen ports and all parameters, so the measurement settings
are always recoverable later.

### How the NTC group readout works

The SchwaRTech/AWI temperature head is a **multiplexed** system: each physical
sensor module is a **node** (`ntc_nodes`, e.g. `90;91;92;93;94`). You cannot read
all nodes freely at once — you tell the head which nodes form the current **node
array** with a `NODES 90 91 92 …` command, and it then streams reading lines for
exactly those nodes. **One group = one node array read in one burst.**

`ntc_groups` controls this as `sensors_per_group ; measurement_points_per_group`:

- **`sensors_per_group`** — how many nodes go into each group. The flat
  `ntc_nodes` list is chunked into blocks of this size. With `5` and five nodes
  you get **one** group `[90,91,92,93,94]`; with ten nodes you get **two**
  (`[90-94]`, `[95-99]`).
- **`measurement_points_per_group`** — how many reading lines are collected for a
  group before switching to the next (a few extra lines are read to absorb the
  echo/confirmation the head emits when the node array changes).

`ntc_readout` picks which channels each node outputs (`NTC1`, `NTC2`, `TestSB`,
…); `DateTime`/`TempADC` are per-line, the rest are per-node.

**In the `_ntc.txt` file** the readout is written as tagged blocks, not one wide
table. Each cycle writes a `Group<n>;` header followed by ~`measurement_points`
data rows, then moves to the next group (and later loops back):

```
Group1; SecondsElapsed; DateTimePC; N94_NTC1 | N94_NTC2 | N94_TestSB || N96_NTC1 | N96_NTC2 | N96_TestSB
Group1; 9.19; 2026-07-02 15:59:58.58; 5812827 | 5732217 | 5648185 || 5540969 | 5471459 | 5400559
Group1; 13.77; 2026-07-02 16:00:03.15; 5310211 | 5253038 | 5192965 || 5113788 | 5066173 | 5013578
…
Group1; SecondsElapsed; DateTimePC; …          ← header repeats each cycle
```

- Within a data row, `|` separates channels **within** a node and `||` separates
  **nodes**. Every row of a group fills all that group's node columns — no blanks.
- With **multiple groups**, `Group1` blocks and `Group2` blocks alternate; a node
  that is not in the current group simply does not appear in that block (it shows
  up in its own group's block). The `Group<n>;` tag exists to separate them again.
- The header line is rewritten every cycle; on read-in, repeated headers and the
  `New Node Array:` / echo lines are filtered out. Single-group runs therefore
  reduce to one clean wide table. (Multi-group column-name handling is a known
  limitation — see `TODO.md`.)

---

## Automated bath-driven runs — `calibration_auto.py`

Same logging as the legacy tool, but it also steps the **Isotech Libra 785** bath
through a list of temperature plateaus. Isotech baths use a Eurotherm 2000-series
controller speaking **Modbus over RS232** (the same path Isotech's own *Cal
NotePad* uses); `bath.py` wraps that controller.

```bash
python3 -m pip install -r requirements.txt      # now also installs minimalmodbus
python3 calibration_auto.py --dry-run           # connect + read the bath, don't move it
python3 calibration_auto.py                      # full automated run
```

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

Common flags: `--encoding float|int1|int2|int3` (value scaling, default `float`),
`--slave N`, `--tol`, `--window`, `--timeout`. If PV/SP read back wrong, the
encoding or baud/parity (in `bath.py`) is off — see the header notes.

**Per plateau (`calibration_auto.py`):** (optional ramp →) set setpoint → wait until the bath is stable
(PV inside `stability_tol` for `stability_window` minutes) → measure for
`plateau_minutes` → next. A fourth output file
`<experiment>_<time>_plateaus.txt` records each plateau's setpoint and timestamps
(commanded / stable / measurement start+end) so the stable window can be sliced
out afterwards. The live status line shows bath PV/SP, the **SPRT temperature**
(via `sprt.py`) **and** the **raw NTC1 temperature per node** (via `ntc.py`) so you
see the whole picture at a plateau. Both conversions are **display only** and use
the nominal formulas from the R workflow — SPRT: the 2-point ratio calibration;
NTC: `NTCcounts2temp` (β=3380, R25=10 kΩ), i.e. uncalibrated raw temperature. The
authoritative per-sensor calibration stays in the R pipeline.

⚠️ **Verify once against the real controller** (see `bath.py` header): serial
baud/parity, Modbus slave address, and the **value encoding**
(`bath_encoding: float` vs `int1/int2/int3`). Run `--dry-run` first — if PV/SP
read back as sensible numbers, the settings are right.

Bath-automation config keys (in `param_combined.txt`, ignored by the legacy tool):

| Key                 | Meaning                                                          |
|---------------------|------------------------------------------------------------------|
| `bath_port`         | Optional bath-controller port hint (pre-selection only)          |
| `bath_slave`        | Modbus slave address (default `1`)                               |
| `bath_encoding`     | `float` \| `int1` \| `int2` \| `int3` — register value scaling   |
| `plateaus`          | 1–20 setpoints in °C, in run order (`;`-separated)               |
| `plateau_minutes`   | Dwell after stability; one value → all, or one per plateau       |
| `ramp_c_per_min`    | Optional approach ramp; empty → max speed; one → all, or per pl. |
| `stability_tol`             | °C band around the setpoint counting as "arrived"        |
| `stability_window`          | minutes PV must stay in band before measuring            |
| `stability_timeout_per_10k` | max wait scales with step: minutes per 10 K of travel    |
| `stability_timeout_min`     | floor (minutes) for small/zero steps                     |

If a plateau is still not stable when its (step-scaled) timeout elapses, the run
**measures anyway** and moves on — it never blocks the whole schedule.

---


## Repository contents

| File                  | Purpose                                             |
|-----------------------|-----------------------------------------------------|
| `calibration_log.py`  | Legacy logger (manual bath setpoints)               |
| `calibration_auto.py` | Automated logger + bath plateau control             |
| `bath.py`             | Isotech/Eurotherm bath control (Modbus RTU)         |
| `sprt.py`             | SPRT ratio → temperature (live display)             |
| `ntc.py`              | NTC raw counts → temperature (live display)         |
| `test_bath.py`        | Offline test suite (in-process fake slave)          |
| `tools/bath_sim.py`   | Modbus-RTU bath simulator (serial-level testing)    |
| `param_combined.txt`  | Configuration (logging + bath automation)           |
| `requirements.txt`    | Python dependencies                                 |
| `TODO.md`             | Known limitations / future work                     |
