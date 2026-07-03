# Output data formats — for the R calibration pipeline

Every run of `calibration_log.py` (legacy) or `calibration_auto.py` (bath-driven)
writes to `./Output/` **four** files that share a stem
`<experiment>_<YYYYMMDD-HHMMSS>_`:

| File            | Content                                             | Written by |
|-----------------|-----------------------------------------------------|------------|
| `…_microk.txt`  | SPRT reference stream (MicroK bridge)               | both tools |
| `…_ntc.txt`     | NTC head stream (all nodes, all NTC channels)       | both tools |
| `…_meta.txt`    | Run metadata + verbatim copy of the parameter file  | both tools |
| `…_plateaus.txt`| Per-plateau setpoints and stable-window timestamps  | `calibration_auto.py` only |

All timestamps are the **same PC wall clock** (naive local time, microseconds).
Match the two data streams by timestamp; the plateau file tells you which time
windows are the stable measurement points. There is **no** shared row index
between the microk and ntc files — they are logged by independent threads at
different cadences.

---

## 1. `…_microk.txt` — SPRT reference

Semicolon-separated, **no header line**, one row per MicroK reading. Six fields:

```
0.060677194595336915;2026-07-03 18:17:28.190392;2.5394747240E-001;0.56mA;Channel2;1
```

| # | field         | type      | meaning |
|---|---------------|-----------|---------|
| 1 | `time_min`    | numeric   | minutes since this logger thread started |
| 2 | `timestamp`   | POSIXct   | PC wall-clock time, `%Y-%m-%d %H:%M:%OS` |
| 3 | `ratio` (W)   | numeric   | **the MicroK ratio reading** R_SPRT/R_ref (dimensionless), scientific notation. NOT ohms — the reference resistor forms the ratio internally. |
| 4 | `current`     | character | sense current as text, e.g. `0.56mA` (keep as string) |
| 5 | `channel`     | character | which SPRT, e.g. `Channel2` (glass SPRT), `Channel3` (670SL) |
| 6 | `index`       | integer   | sequential counter within this file |

Notes for the reader:
- **Only the SPRT channel(s) are logged** — the reference channel (config
  `microk_channels: Reference;SPRT1[;SPRT2]`, here `1;2`) is used only to build
  the ratio and never appears as a row.
- With two SPRTs configured, rows of `Channel2` and `Channel3` **interleave**;
  split by field 5 before converting.
- SPRT temperature = `ratio → T` via the channel's calibration. `lib/ReadMicroKandGetPlateaus.R::read_microk_file()` already does this (it calls
  `SPRTglas_R2T` on field 3; despite the name, field 3 is the ratio, not R).

---

## 2. `…_ntc.txt` — NTC head stream  (needs a group-aware parser)

This is **not** a flat single-header table — `lib/read_ntc_head_file.R` will *not*
parse it correctly, because (a) the sensors are split into **groups**, each with
its **own repeated header**, and (b) the NTC values live in **one** semicolon
field, internally delimited by `|` and `||`.

Every row has exactly **four** `;`-separated fields:

```
Group<g>; <SecondsElapsed>; <DateTimePC>; <datablock>
```

The `<datablock>` (4th field) is a block of **nodes** separated by `||`, each node
holding its **channels** separated by `|`, in the order set by `ntc_readout`
(here `NTC1 | NTC2 | TestSB`). Example header + data for one group of 4 nodes:

```
Group1; SecondsElapsed; DateTimePC; N88_NTC1 | N88_NTC2 | N88_TestSB || N89_NTC1 | … || N91_NTC1 | N91_NTC2 | N91_TestSB
Group1; 18.3316; 2026-07-03 18:18:33.959481; 864418 | 859990 | 861490 || 861084 | 863472 | 863099 || … || 861775 | 861527 | 861823
```

### Row types (per group, in this cyclic order)
| field 2 (`SecondsElapsed`) | field 4 (`datablock`)              | meaning |
|----------------------------|------------------------------------|---------|
| the literal `SecondsElapsed` | `Nxx_NTC1 \| Nxx_NTC2 \| …`        | **column header** for this group (gives the node/channel labels) |
| numeric                    | `New Node Array: 88 89 90 91`      | meta: which nodes this group now covers |
| numeric                    | *(empty)*                          | meta: skip |
| numeric                    | `Nxx_NTC1 \| …` (label text again) | an **echoed** header — skip |
| numeric                    | `864418 \| 859990 \| …` (all numbers) | **DATA** — the raw ADC counts |

**Identify a data row** = field 2 parses as a number **and** field 4 contains
digits but none of `NTC`, `New Node Array` (this is exactly what the Python
`ntc.ntc_from_row()` does). Anchor each data row to the **nearest preceding
header of the same `Group<g>`** to get its `Nxx_<channel>` labels — do not assume
a fixed column order across groups.

### Groups
`ntc_groups: <sensors_per_group> ; <measurement_points_per_group>` (here `4;2`).
The head is multiplexed: the logger cycles Group1→Group2→…, and per cycle emits
`measurement_points_per_group` data rows per group. All nodes appear once per
cycle across the groups. For calibration, just collect **all data rows whose
timestamp falls in a plateau window** and average per node/channel.

### counts → resistance → temperature
Raw ADC counts (e.g. `864418`) convert exactly as in
`CalibrationChains/lib/SPRTRtoT_NTCtoR.R` (mirrored in `ntc.py`):

```
ADC_FULLSCALE = 33554432        # 2^25
DIVIDER_OHM   = 499000
h = (-counts * 1e6) / (counts - ADC_FULLSCALE)
R = (h * DIVIDER_OHM) / (DIVIDER_OHM - h)          # ohms
```

Higher counts ⇒ higher R ⇒ colder. `R → T` is the per-sensor lab fit in your
pipeline. (For reference, the live display uses the universal mean-S4 curve
`1/T[K] = a + b·lnR + c·lnR² + d·lnR³`, R in ohms, `a,b,c,d =
8.4229499262e-04, 2.7615486685e-04, -3.1654916185e-06, 3.0727486494e-07`.)

### Gotchas
- **Disconnected / open input:** raw counts `> 10 000 000` mean the channel is not
  connected — drop them (do **not** convert).
- **`TestSB` is a second NTC** (wired as NTC2), so it converts like any NTC. The
  R helper `read_ntc_head_file(remove_TestSB = TRUE)` drops it by default — set
  `FALSE` if you want it.
- Node label is the text before `_<channel>` (`N88` → node 88).

---

## 3. `…_plateaus.txt` — the stable measurement windows  ⭐

This is the key to slicing the calibration points cleanly — no need to detect
plateaus from the SPRT curve. Header comment lines start with `#`; data rows are
`;`-separated:

```
# columns: idx; setpoint_C; ramp_C_per_min; t_command; t_stable; t_dwell_start; t_dwell_end; stable_ok
1; -5.0; 1.0; 2026-07-03 18:17:26.152391; 2026-07-03 18:28:32.213261; 2026-07-03 18:28:32.213304; 2026-07-03 18:30:32.616045; True
```

| field          | meaning |
|----------------|---------|
| `idx`          | plateau number (1-based, run order) |
| `setpoint_C`   | commanded bath setpoint |
| `ramp_C_per_min` | approach ramp used (`off` = max speed) |
| `t_command`    | when the setpoint was sent |
| `t_stable`     | when the bath was declared stable (PV in band for `stability_window`) |
| `t_dwell_start`| **start of the measurement window** |
| `t_dwell_end`  | **end of the measurement window** |
| `stable_ok`    | `True` = reached the band; `False` = stability timed out, measured anyway → treat this plateau's data with care |

**Use `[t_dwell_start, t_dwell_end]` per plateau** to select rows from both the
microk and ntc streams; average within the window to get one SPRT reference
temperature and one NTC counts value per node/channel per setpoint. Those pairs
are your calibration points. Skip / flag plateaus where `stable_ok = False`.

---

## 4. `…_meta.txt` — run metadata

Free-form `key : value` header, then a **verbatim copy of the parameter file**,
then (auto runs) a `--- Bath automation ---` block. Machine-useful bits:

- `Node IDs` — the sensor node list (also in the ntc headers).
- `NTC readout` — the channel order in each node block.
- `Groups` — `<sensors/group>, <measurement points>`.
- The verbatim param copy carries the full provenance (bath protocol, ramps,
  stability settings) for the record.

Parse it only if you want provenance; everything needed for the calibration is in
the three data files above.

---

## Existing code you can reuse — and one trap to fix

`recalib2sensors/check_repro.R` already has working helpers for this exact format:
`read_microk_file()` (from `lib`), `read_logger_2026()` (NTC parse),
`match_ntc_to_sprt()`, `NTCcounts2R()`, `S4_predict_T_C()`. Reuse them.

⚠️ **`read_logger_2026()` is single-group only.** It reads the channel labels from
**line 1** and applies them to every data row. That was fine for the 2-sensor
reference run (one group), but the 12-sensor run here has **3 groups** (Group1
N88–91, Group2 N92–96, Group3 N97/98/99/29), each with **different** node columns.
Because every group's data row has the same *number* of values (4 nodes × 3
channels = 12), the length check passes and Group2/Group3 counts get **silently
mislabelled as Group1's nodes**. You must make it **group-aware**: key each data
row to the nearest preceding header **of its own `Group<g>`** and take the labels
from there (exactly as the Python `ntc.ntc_from_row()` does). The fix is small —
carry the current group's header while scanning, or `split()` the lines by field 1
and parse each group with its own header.

## Recommended R consumption recipe

1. `read_microk_file()` → SPRT stream; split by `channel`; you get `TSPRT(t)`.
2. **Group-aware NTC parse** (see the trap above): for each data row, look up the
   nearest same-group header, emit long-format `(timestamp, node, channel, counts)`;
   drop counts `> 1e7`; convert counts→R→T with `NTCcounts2R()` + the per-sensor fit.
3. `read.table(…, comment.char="#")` the plateau file → windows.
4. For each plateau row with `stable_ok == TRUE`, take microk and ntc rows with
   `t_dwell_start ≤ timestamp ≤ t_dwell_end`, average → one `(T_SPRT, counts)` per
   node/channel. These are the calibration points; fit R→T per sensor as usual.

Matching is purely by wall-clock timestamp (same PC clock in every file); there is
no need to align indices or sample rates.
