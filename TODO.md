# TODO

## Multi-group NTC readout: reader cannot separate groups by column names

**Status:** known limitation, low priority (single-group is the normal case).

When more than one NTC **group** is used (i.e. `ntc_nodes` split into several
node-arrays via `Nr_NTCs_group` in `param_combined.txt`), `calibration_log.py`
writes the file correctly: each group's rows are tagged `Group<n>;` and carry a
repeated header with that group's node columns (e.g. `N90_NTC1 | …` vs
`N95_NTC1 | …`).

The R reader `read_ntc_head_file()` (in the sibling repo
`../CalibrationChains/lib/read_ntc_head_file.R`) takes the column names **only
from the first line** and keeps every data row with a matching field count. If
two groups have the **same number of nodes** but different IDs, the field counts
match, so group-2 rows get mislabelled with group-1's column names.

**Fix idea:** split the file by the `Group<n>;` tag first, apply each group's own
(repeated) header, then merge the groups by wall-clock timestamp — the same
timestamp-matching used everywhere else. The `Group<n>;` tag is already written
for exactly this purpose; the reader just doesn't use it yet.

**Workaround today:** use one group per head file (the current setup), which
produces a single consistent column layout that the reader handles cleanly.
