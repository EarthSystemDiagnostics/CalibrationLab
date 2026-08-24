# Reducing the Libra 785 overshoot — PID tuning notes

Working notes on how to reduce the bath's approach overshoot / oscillation.
Nothing here is wired into the calibration code yet — it's a plan for later.

## The problem
The Isotech **Libra 785** (Eurotherm **3504** controller) slews only ~2 °C/min at
full power and still **overshoots ~2 °C** on a step (dead time between heater and
sensor + the controller's PID). Example: −20 → 0 °C swings to ~+2 °C before settling.

## What we can reach over serial (EI-Bisynch, 7E1, addr 1)
The PID is a normal parameter set, readable **and** writable over the same link as
the setpoint `SL`. Eurotherm mnemonics:

| Mnemonic | Meaning |
|---|---|
| `XP` | proportional band (P) |
| `TI` | integral time / reset (I) |
| `TD` | derivative time / rate (D) |
| `HB` / `LB` | cutback high / low — **the anti-overshoot feature** on approach |
| `MR` | manual reset (bias) |
| `AT` | auto-tune enable |
| `RR` | setpoint rate limit (already used: 1 °C/min) |

**Prerequisite before changing anything:** read `XP/TI/TD/HB/LB` first (read-only)
to confirm they respond and to see the current values — and to check whether
writes are allowed (some parameters need a higher access level):
```
python3 bisynch.py --port /dev/cu.usbserial-FT3GCNKB1 --addr 1 --read XP   # TI, TD, HB, LB too
```

## Gain scheduling — check before tuning
The 3504 may use **gain scheduling** (different PID sets per temperature zone), so a
single `XP/TI/TD` read is only the *active* set. How to tell whether it's enabled:
- **Front panel:** *Loop → PID → "Gain Sched"*. `None`/`Off` = single set; `Set`/`PV`/
  `SP`/… = active, with `Num Sets` (2–3), `Active Set`, per-set PID + boundaries.
- **Empirical (serial, no manual):** read `XP/TI/TD` at a low temperature, move the
  bath to a very different one, read again — if they change, scheduling is active.
- **Manual:** Eurotherm **3500 Series Engineering Handbook, HA027988** documents the
  feature + parameters (it says the feature exists, not whether *your* unit has it on).

If scheduling is on, tune **per set** (start at each set's zone temperature).

## Optimization ladder (cheap → heavy)
1. **Built-in auto-tune (`AT`) first.** The 3504 self-tunes via a relay/oscillation
   experiment → computes `XP/TI/TD`. Minutes to ~1 h per point, *designed* for this.
   Tune each gain-schedule set at its zone. Often fixes the overshoot outright.
2. **Cutback `HB`/`LB`.** Small, targeted anti-overshoot lever; doesn't touch the
   core PID. Good second step after auto-tune.
3. **Rate limit `RR`.** Keep as a safety cap (already 1 °C/min).
4. **Automated search — last resort, and scoped small** (see below), not a blind grid.

## If we build an automated search program
Design principles (would be a standalone tool, like `tools/port_detect.py`, that
does NOT touch the calibration code):
- **Signal = the BATH PV, not the SPRT.** The SPRT is a large, slow probe that lags;
  the controller regulates its own PV, and *that* is what oscillates. Use the SPRT
  only for a final stability check of the real measurement point.
- **One standard test per trial:** a fixed step (e.g. −20 → 0), record PV, compute a
  **cost** = overshoot + settling time + ∫|error|.
- **Few parameters + smart optimizer:** e.g. just `XP`+`TI`, or just `HB`/`LB`, via
  coordinate-descent or Nelder–Mead → ~15–30 trials, not hundreds.
- **Bottleneck:** a trial is ~30–60 min (slow slew). ~20 trials ≈ **~1 day**. A full
  `XP×TI×TD` grid would be ~4–5 days — hence the optimizer, not brute force.
- **Safety:** read & **save the original PID first**; hard bounds per parameter;
  **auto-restore** on finish/Ctrl-C; the over-temp limiter stays active; log
  resumably to CSV.

## Recommended next steps
1. Read `XP/TI/TD/HB/LB` (confirms serial write access + current tuning).
2. Check gain scheduling (panel or the two-temperature read test).
3. Try **one** auto-tune at a typical temperature; see if it's enough.
4. Only if needed, build the scoped search harness (bath-PV cost, backup/restore).

## References
- Eurotherm **3500 Series Engineering Handbook — HA027988** (PID, gain scheduling,
  cutback, auto-tune, comms parameters).
- Isotech **Libra 785** bath manual (points to the Eurotherm controller for details).
- Related: [[libra785-two-controllers]] (how the bath is reached over serial).
