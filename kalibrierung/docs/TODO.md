# TODO / known limitations

## 1. Bath overshoot on the approach — not tuned yet
The Libra 785 slews only ~2 °C/min and overshoots ~2 °C on a step; we work around
it with `ramp_c_per_min: 1`, which costs time but keeps the overshoot at ~1 °C.
The real fix is the 3504's own PID / cutback / auto-tune, reachable over the same
EI-Bisynch link. Plan, parameters and safety rules: **`PID_TUNING.md`**. Nothing of
it is wired into the calibration code — start with a read-only `--read XP/TI/TD/HB/LB`.

## 2. R reader: old multi-group NTC files
**Status:** only affects files recorded **before** 2026-07-07 (commit `9e94356`).

Since that commit all nodes are read as **one** array, so every file has a single
header on line 1 and the R reader `read_ntc_head_file()` / `read_logger_2026()`
(sibling repo `../CalibrationChains/lib/`) is correct as it stands.

Older files were multiplexed into `Group1…GroupN`, each with its own repeated
header. Those readers take the column names **only from the first line** and keep
every data row with a matching field count — so if two groups have the same number
of nodes but different IDs, group-2 rows get mislabelled with group-1's columns.
**Fix, if such a file has to be re-read:** split by the `Group<n>;` tag first, apply
each group's own header, then merge by wall-clock timestamp. See DATA_FORMATS.md
§ "legacy multi-group files".

## 3. R reader: `TOFFMS` field
Files written since commit `ee9593d` carry a 5th `;` field `TOFFMS=…` (per-value
timestamps). Old 4-field readers keep working only if they split into at most 4
fields and strip anything after a further `;` in the data block — otherwise the last
count comes back as `"4952001; TOFFMS=0"`. Using `TOFFMS` (pairing each NTC value
with the SPRT interpolated to that instant) is what makes the ~10 mK bath
oscillation cancel instead of aliasing; see DATA_FORMATS.md, "Recommended R
consumption recipe".

## 4. Untested paths
- `microk_channels` with **two** SPRTs (`1;2;3`) is implemented (rows interleave by
  `ChannelN`, the gate uses the first SPRT unless `gate_channel` is set) but the
  recent runs all used one SPRT — check it once before relying on it.
- `sprt.py` carries the **H1-2025** fixed-point ratios for both SPRTs (display only).
  After a recalibration they must be updated in step with
  `CalibrationChains/lib/SPRTRtoT_NTCtoR.R`.
- The Modbus path (`bath_protocol: modbus`, `bath.py`) is only exercised against
  `tools/bath_sim.py` and the Libra's over-temp limiter, not against a Series-2000
  controller in this lab.
