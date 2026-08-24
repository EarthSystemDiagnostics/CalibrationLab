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

## 2. `…_ntc.txt` — NTC head stream

**Current format (since commit `9e94356`, 2026-07-07): one single node array.**
The logger issues `NODES …` **once** at startup, writes the column header **once**
at the top of the file and then streams nothing but data rows. Every row still
carries the `Group1;` tag for backward compatibility — but there is only ever one
group. Files recorded *before* that change contain several groups with repeated
headers; see [legacy multi-group files](#legacy-multi-group-files-old-runs-only).

It is still **not** a flat `read.table()` table: the NTC values all live in **one**
semicolon field, internally delimited by `|` and `||`.

Every row has **four** `;`-separated fields (five with `TOFFMS`):

```
Group<g>; <SecondsElapsed>; <DateTimePC>; <datablock>[; TOFFMS=<offsets>]
```

The `<datablock>` (4th field) is a block of **nodes** separated by `||`, each node
holding its **channels** separated by `|`, in the order set by `ntc_readout`
(here `NTC1 | NTC2 | TestSB`). Example header + data for one group of 4 nodes:

```
Group1; SecondsElapsed; DateTimePC; N88_NTC1 | N88_NTC2 | N88_TestSB || N89_NTC1 | … || N91_NTC1 | N91_NTC2 | N91_TestSB
Group1; 18.3316; 2026-07-03 18:18:33.959481; 864418 | 859990 | 861490 || 861084 | 863472 | 863099 || … || 861775 | 861527 | 861823; TOFFMS=0|763|1526|2290|…
```

**⭐ `TOFFMS` — per-value timestamps (token-accurate logger).** The head measures
sensors **sequentially**, so a many-node line streams over tens of seconds to a
minute; the bath meanwhile oscillates ~10 mK on a ~2 min limit cycle. One row-level
timestamp would alias that into several mK of per-sensor error. The 5th field
`TOFFMS=o0|o1|…` gives one integer per value: **milliseconds from the first value**
(whose time is the row's `DateTimePC`), in the SAME left-to-right order as the
counts. So value *k*'s absolute time is `DateTimePC + o_k/1000 s`. The field is
appended after a `;`, so a reader that splits into 4 fields keeps working (the
counts block is unchanged); older files simply lack it. `len(TOFFMS) == nodes ×
channels`. **Use it** (see the matching recipe below) — a 150-value line can span
~2 min, half an oscillation period.

### Row types
| field 2 (`SecondsElapsed`) | field 4 (`datablock`)              | meaning |
|----------------------------|------------------------------------|---------|
| the literal `SecondsElapsed` | `Nxx_NTC1 \| Nxx_NTC2 \| …`        | **column header** — written **once**, as the first line of the file |
| numeric                    | `864418 \| 859990 \| …` (all numbers) | **DATA** — the raw ADC counts |

The head's chatter (`New Node Array: …` banners, echoed headers, blank keep-alive
lines) is classified structurally by the logger (`calibration_log.classify_head_line`)
and **never reaches the file**; incomplete rows (Ctrl-C or a mid-line stall) are
dropped as well, so every data row is full width.

**Identify a data row** defensively anyway = field 2 parses as a number **and**
field 4 contains digits but none of `NTC`, `New Node Array` (exactly what the Python
`ntc.ntc_from_row()` does). Take the column labels from the file's header line; on a
long run it scrolls out of a tail window, so a reader may also fall back to the
layout implied by `ntc_nodes` × `ntc_readout` in the meta file.

<a id="legacy-multi-group-files-old-runs-only"></a>
### `ntc_groups` and legacy multi-group files (old runs only)
`ntc_groups: <sensors_per_group> ; <measurement_points_per_group>` is a **legacy
config field**. It is still required in the param file and echoed into the meta
file, but it no longer chunks the nodes — all nodes go into the one array.

Runs recorded before 2026-07-07 were multiplexed: the logger cycled
Group1→Group2→…, each group carrying its **own repeated header** with different
node columns. To read such a file, anchor each data row to the **nearest preceding
header of its own `Group<g>`** — never to line 1 (see the trap below). New files
need none of that.

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
`;`-separated. **Read `gate_mode:` from the header** — the columns differ:

**`gate_mode: drift` (default)** — plateaus are gated on the SPRT reference dT/dt.
A plateau ends when the trailing-window drift and scatter are both under limit;
the row is written when the gate accepts, and the window is that trailing average:

```
# gate_mode: drift
# columns: idx; setpoint_C; ramp_C_per_min; t_command; t_stable; t_dwell_start; t_dwell_end; gate_ok; drift_mK; sd_mK; n
1; -5.0; 1.0; 2026-07-03 18:17:26; 2026-07-03 18:40:02; 2026-07-03 19:12:31; 2026-07-03 19:42:31; True; 1.84; 5.11; 612
```

| field          | meaning |
|----------------|---------|
| `idx`          | plateau number (1-based, run order) |
| `setpoint_C`   | commanded bath setpoint |
| `ramp_C_per_min` | approach ramp used (`off` = max speed) |
| `t_command`    | when the setpoint was sent |
| `t_stable`     | when the bath PV reached the setpoint band (coarse) |
| `t_dwell_start`| **start of the measurement window** (= `t_dwell_end − gate_window_min`) |
| `t_dwell_end`  | **end of the measurement window** (= the moment the gate accepted) |
| `gate_ok`      | `True` = drift/sd gate passed; `False` = soak cap hit, window recorded but **flagged** → check the reference drift |
| `drift_mK`     | fitted drift over the window at acceptance (mK), `NA` if no SPRT data |
| `sd_mK`        | scatter about the fit line (mK) |
| `n`            | SPRT samples in the window |

Note: in drift mode an **interrupted soak leaves no row** (nothing was accepted) —
recover such a plateau from the raw logs if needed.

**`gate_mode: fixed` (legacy)** — 8 columns, row written at dwell start:

```
# columns: idx; setpoint_C; ramp_C_per_min; t_command; t_stable; t_dwell_start; t_dwell_end; stable_ok
1; -5.0; 1.0; 2026-07-03 18:17:26; 2026-07-03 18:28:32; 2026-07-03 18:28:32; 2026-07-03 18:30:32; True
```
`stable_ok` = `True` reached the band / `False` stability timed out, measured anyway.

**Either mode: use `[t_dwell_start, t_dwell_end]` per plateau** to select rows from
both the microk and ntc streams; average within the window to get one SPRT
reference temperature and one NTC counts value per node/channel per setpoint.
Those pairs are your calibration points. Skip / flag plateaus where the OK column
(`gate_ok` / `stable_ok`) is `False`. A positional reader that takes the first 8
columns works for both modes (the drift columns are appended).

---

## 4. `…_meta.txt` — run metadata

Free-form `key : value` header, then a **verbatim copy of the parameter file**,
then (auto runs) a `--- Bath automation ---` block. Machine-useful bits:

- `Node IDs` — the sensor node list (also in the ntc header line). Together with
  `NTC readout` this is the full column layout, so a reader can reconstruct the
  header even from a tail of the ntc file.
- `NTC readout` — the channel order in each node block.
- `Groups` — echo of the **legacy** `ntc_groups` field; it no longer chunks the
  nodes and can be ignored.
- The verbatim param copy carries the full provenance (bath protocol, ramps,
  stability settings) for the record.

Parse it only if you want provenance; everything needed for the calibration is in
the three data files above.

---

## Existing code you can reuse — and one trap to fix

`recalib2sensors/check_repro.R` already has working helpers for this exact format:
`read_microk_file()` (from `lib`), `read_logger_2026()` (NTC parse),
`match_ntc_to_sprt()`, `NTCcounts2R()`, `S4_predict_T_C()`. Reuse them.

⚠️ **`read_logger_2026()` is single-group only** — reading the channel labels from
**line 1** and applying them to every data row.

- **New files (single array, since 2026-07-07): that is now correct.** Line 1 *is*
  the one and only header, and every row belongs to it. No change needed.
- **Old multi-group files: still a silent trap.** The 12-sensor runs have 3 groups
  (Group1 N88–91, Group2 N92–96, Group3 N97/98/99/29) with **different** node
  columns but the same *number* of values (4 nodes × 3 channels = 12), so the length
  check passes and Group2/Group3 counts get **mislabelled as Group1's nodes**. To
  re-read such a file, key each data row to the nearest preceding header **of its
  own `Group<g>`** (as the Python `ntc.ntc_from_row()` does) — carry the current
  group's header while scanning, or `split()` by field 1 and parse per group.
- Both cases also need the 5th `TOFFMS` field to be tolerated: split into 4 fields
  and strip anything after a further `;` in field 4.

## Recommended R consumption recipe

1. `read_microk_file()` → SPRT stream; split by `channel`; you get `TSPRT(t)` as a
   dense series (~3.6 s cadence — resolves the ~2 min bath oscillation with margin).
2. **NTC parse**: take the labels from the file's header line (for old multi-group
   files: from the nearest same-group header — see the trap above) and emit
   long-format `(timestamp, node, channel, counts)`.
   **⭐ Use `TOFFMS` for the per-value time**: value *k*'s time is
   `DateTimePC + TOFFMS[k]/1000 s`, NOT the row time (the row can span ~2 min).
   Drop counts `> 1e7`; convert counts→R→T with `NTCcounts2R()` + the per-sensor fit.
3. `read.table(…, comment.char="#")` the plateau file → windows. Read only the
   first 8 columns positionally (works for both `gate_mode`s); the 8th is the OK
   flag (`gate_ok`/`stable_ok`).
4. **Per-sample matching (⭐ do NOT average the two streams separately).** For each
   plateau row where the OK flag is `TRUE`, take each NTC value whose per-value time
   is in `[t_dwell_start, t_dwell_end]` and pair it with the SPRT **interpolated to
   that exact time** (`approx(TSPRT_t, TSPRT, xout = ntc_time)`). *Then* average the
   per-sample pairs → one `(T_SPRT, counts)` per node/channel. Because the ~10 mK /
   2 min bath oscillation is common-mode on SPRT and NTC, pairing at the same instant
   makes it **cancel**; averaging the window-means separately instead lets the block
   cadence beat against the oscillation and injects a systematic per-sensor bias of
   several mK. `drift_mK`/`sd_mK` are QC only.

Matching is purely by wall-clock timestamp (same PC clock in every file). The one
thing that matters at the mK level is pairing each NTC value with the SPRT at its
**own** time — hence `TOFFMS` + interpolation, not row time + window means.
