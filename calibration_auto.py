#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Automated calibration run: drive the Isotech bath through a list of temperature
plateaus while logging MicroK (SPRT) + SchwaRTech/AWI head (NTCs) continuously.

This is the "with bath control" companion to calibration_log.py (which stays the
passive, manual-setpoint legacy tool). Everything about the two loggers is reused
unchanged -- this script only adds the bath orchestration on top:

    for each plateau:
        (optional) set an approach ramp rate
        command the setpoint
        wait until the bath is stable (PV in tolerance band for a while)
        dwell / measure for the configured number of minutes
        record the plateau's timestamps

The MicroK and NTC loggers run for the whole session, exactly as in the legacy
tool, so the two data streams still match up by wall-clock timestamp. An extra
file  <exp>_<time>_plateaus.txt  records, per plateau, the setpoint and the
timestamps (commanded / stable / measurement start / measurement end) so you can
slice out each plateau's stable window afterwards.

Usage:
    python3 calibration_auto.py                     # uses param_combined.txt
    python3 calibration_auto.py --param x.txt        # different config file
    python3 calibration_auto.py --exp Run7           # override experiment name
    python3 calibration_auto.py --dry-run            # read bath only, don't move it

Stop: Ctrl-C -> loggers shut down cleanly; the bath is left at its last setpoint.

See param_combined.txt for the bath-automation keys and bath.py for the
controller-specific settings (encoding, baud, registers) that must be verified
once against the real Eurotherm controller.
"""

import os
import sys
import time
import argparse
import threading
from datetime import datetime, timedelta

# Reuse the legacy logging machinery unchanged.
from calibration_log import (
    read_config, pick_port, microk_worker, logger_worker, write_meta,
)
from bath import Bath
from bisynch import BisynchBath
import sprt
import ntc
import gate


def make_bath(bath_port, b):
    """Construct the right controller for the configured protocol.

    Bisynch (default) drives the Eurotherm 3504 directly; modbus keeps the old
    Series-2000 path. Both expose the same read_pv/set_setpoint/... interface.
    """
    if b["protocol"] == "bisynch":
        return BisynchBath(bath_port, address=b["address"])
    return Bath(bath_port, slave=b["slave"], use_float=b["use_float"],
                decimals=b["decimals"])


# --------------------------------------------------------------------------
# Bath-automation configuration (parsed from the same parameter file)
# --------------------------------------------------------------------------
# Upper bound on the number of plateaus -- just a sanity guard against a runaway
# config, not a hardware limit. Raise it if you genuinely need more.
MAX_PLATEAUS = 100


def _parse_kv(path, cfg):
    """Merge `key: value` lines from `path` into cfg (later files win)."""
    with open(path, "r") as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, val = line.split(":", 1)
            val = val.split("#", 1)[0]          # drop inline comments after the value
            cfg[key.strip().lower()] = val.strip()
    return cfg


def read_bath_config(param_path):
    """Parse the bath-automation keys from the (single) parameter file.

    Same key:value / ';'-separated-list style as the rest of the config.
    List rules for plateau_minutes and ramp_c_per_min:
        one value  -> applied to every plateau
        N values   -> one per plateau (N must equal the number of plateaus)

    plateau_minutes means different things per gate_mode:
        fixed  -> the bath dwells exactly this long, then we measure the window.
        drift  -> the MINIMUM soak time before the SPRT drift/sd gate may accept
                  (cold plateaus creep for hours, so give the cold end e.g. 180).
    """
    cfg = _parse_kv(param_path, {})

    def as_list(key):
        return [x.strip() for x in cfg.get(key, "").split(";") if x.strip()]

    b = {}
    b["port_hint"] = cfg.get("bath_port", "")
    # Which protocol drives the controller. The Isotech Libra 785's Eurotherm 3504
    # speaks EI-Bisynch (7E1), which is the default. 'modbus' is kept for units /
    # controllers configured for Modbus RTU (and for the over-temp limiter).
    b["protocol"] = cfg.get("bath_protocol", "bisynch").lower()
    if b["protocol"] not in ("bisynch", "modbus"):
        sys.exit(f"bath_protocol must be 'bisynch' or 'modbus' (got {b['protocol']!r})")

    # Bisynch device address (GID/UID); Modbus slave address. Same key works for both.
    b["address"] = int(cfg.get("bath_address", cfg.get("bath_slave", "1")))
    b["slave"]   = b["address"]

    # Encoding is a Modbus-only concept (scaled int vs IEEE float). Bisynch is
    # ASCII, so it is ignored there; only validated when protocol == modbus.
    enc = cfg.get("bath_encoding", "int1").lower()
    if b["protocol"] == "modbus":
        if enc == "float":
            b["use_float"], b["decimals"] = True, 1
        elif enc in ("int0", "int1", "int2", "int3"):
            b["use_float"], b["decimals"] = False, int(enc[-1])
        else:
            sys.exit(f"bath_encoding must be one of float|int0|int1|int2|int3 (got {enc!r})")
    else:
        b["use_float"], b["decimals"] = False, 1   # unused for bisynch

    plateaus = [float(x) for x in as_list("plateaus")]
    if not (1 <= len(plateaus) <= MAX_PLATEAUS):
        sys.exit(f"plateaus: give between 1 and {MAX_PLATEAUS} temperature values (deg C)")
    b["plateaus"] = plateaus
    n = len(plateaus)

    def expand(key, values, what):
        if len(values) == 1:
            return values * n
        if len(values) == n:
            return values
        sys.exit(f"{key}: give one {what} (applies to all) or exactly {n} (one per plateau)")

    mins = [float(x) for x in as_list("plateau_minutes")]
    if not mins:
        sys.exit("plateau_minutes: give the dwell time in minutes (one value, or one per plateau)")
    b["minutes"] = expand("plateau_minutes", mins, "dwell time")

    ramp_raw = as_list("ramp_c_per_min")
    if not ramp_raw:
        b["ramps"] = [None] * n            # empty -> max speed for all plateaus
    else:
        rv = expand("ramp_c_per_min", [float(x) for x in ramp_raw], "ramp rate")
        b["ramps"] = [None if r == 0 else r for r in rv]   # 0 == rate limit off

    b["tol"]        = float(cfg.get("stability_tol", "0.02"))
    b["window_min"] = float(cfg.get("stability_window", "10"))
    # Stability timeout scales with the step size: `per_10k` minutes are allowed
    # for every 10 K the bath has to travel, with a floor for small/zero steps.
    # If the bath is still not stable when the timeout elapses, we measure anyway.
    b["timeout_per_10k"] = float(cfg.get("stability_timeout_per_10k", "30"))
    b["timeout_floor"]   = float(cfg.get("stability_timeout_min", "15"))

    # --- Plateau gating (drift = new default; fixed = legacy fixed-time dwell) ---
    # In 'drift' mode a plateau ends when the SPRT reference stops moving:
    # |drift over the trailing window| < gate_drift_mk AND scatter < gate_sd_mk,
    # provided at least the per-plateau min soak (plateau_minutes) has elapsed.
    # The bath's own PV can't judge this (10 mK resolution), so we gate on the
    # MicroK SPRT. 'fixed' keeps the old behaviour (dwell exactly plateau_minutes).
    b["gate_mode"] = cfg.get("gate_mode", "drift").lower()
    if b["gate_mode"] not in ("drift", "fixed"):
        sys.exit(f"gate_mode must be 'drift' or 'fixed' (got {b['gate_mode']!r})")
    b["gate"] = {
        "drift_mk":  float(cfg.get("gate_drift_mk", "3")),
        "sd_mk":     float(cfg.get("gate_sd_mk", "8")),
        "window_min": float(cfg.get("gate_window_min", "30")),
        "poll_s":    float(cfg.get("gate_poll_s", "30")),
        # Hard cap per plateau = its min soak + this, so a plateau that never
        # settles (drifting reference) still ends and gets flagged, not blocks.
        "max_extra_min": float(cfg.get("gate_max_extra_min", "120")),
        # Only for the up-front time estimate: the empirical median settle (~70 min).
        "typical_min": float(cfg.get("gate_typical_min", "70")),
        # Channel label to gate on; empty -> first configured SPRT channel.
        "channel":   cfg.get("gate_channel", "").strip(),
    }
    return b


def plateau_timeout_min(b, delta_k):
    """Timeout in minutes for a step of |delta_k| Kelvin: per_10k per 10 K,
    but never below the floor (so tiny steps still get a fair chance)."""
    return max(b["timeout_floor"], b["timeout_per_10k"] * abs(delta_k) / 10.0)


# --------------------------------------------------------------------------
# Run-time estimate: how long the whole schedule will take
# --------------------------------------------------------------------------
NATURAL_SLEW = 2.0      # deg C/min the Libra 785 manages at full power


def estimate_schedule(b, pv_start):
    """Per-plateau time budget [minutes], starting from bath temperature pv_start.

    Each plateau has a coarse 'settle' part (bath PV reaching the setpoint) and a
    'measure' part. We give a 'probable' and a 'safe' (worst-case) duration:
      safe     = settle timeout + worst-case measure  (a hard upper bound)
      probable = ramp/approach + settle window + probable measure
    Measure part depends on gate_mode:
      fixed -> exactly plateau_minutes.
      drift -> probable = max(min_soak, empirical median ~70 min);
               worst   = min_soak + gate_max_extra (the hard soak cap).
    Ramp time uses the configured rate (capped at the natural slew), or the
    natural slew when no rate limit is set.
    """
    drift = b["gate_mode"] == "drift"
    g = b["gate"]
    rows, prev = [], pv_start
    for sp, minutes, ramp in zip(b["plateaus"], b["minutes"], b["ramps"]):
        delta   = sp - prev
        timeout = plateau_timeout_min(b, delta)
        rate    = NATURAL_SLEW if (ramp is None or ramp <= 0) else min(ramp, NATURAL_SLEW)
        ramp_min = abs(delta) / rate if rate > 0 else 0.0
        settle_prob = min(ramp_min + b["window_min"], timeout)   # can't beat the timeout
        if drift:
            meas_prob = max(minutes, g["typical_min"])           # min soak, but >= median
            meas_safe = minutes + g["max_extra_min"]             # hard soak cap
        else:
            meas_prob = meas_safe = minutes
        rows.append(dict(sp=sp, delta=delta, timeout=timeout,
                         dwell=meas_prob, safe_dwell=meas_safe,
                         probable=settle_prob + meas_prob,
                         safe=timeout + meas_safe))
        prev = sp
    return rows


def _fmt_hm(minutes):
    m = int(round(max(0.0, minutes)))
    return f"{m // 60}h{m % 60:02d}m"


def _clock(epoch):
    return time.strftime("%a %H:%M", time.localtime(epoch))


def remaining_estimate(rows, idx, phase, plateau_elapsed_min, dwell_remaining_min):
    """(probable_min, safe_min) still to go, given we are in plateau `idx` (1-based).
    `phase` = 'SETTLE' (approaching), 'GATE' (soaking, gated) or 'DWELL' (fixed).
    Future plateaus use their full budget."""
    fut = rows[idx:]                                   # plateaus after the current one
    fut_prob = sum(r["probable"] for r in fut)
    fut_safe = sum(r["safe"] for r in fut)
    cur = rows[idx - 1]
    if phase == "SETTLE":
        cur_prob = max(0.0, (cur["probable"] - cur["dwell"]) - plateau_elapsed_min) + cur["dwell"]
        cur_safe = max(0.0, cur["timeout"] - plateau_elapsed_min) + cur["safe_dwell"]
    elif phase == "GATE":                              # soaking: elapsed counts down soak
        cur_prob = max(0.0, cur["dwell"] - plateau_elapsed_min)
        cur_safe = max(0.0, cur["safe_dwell"] - plateau_elapsed_min)
    else:                                              # DWELL: settle already done
        cur_prob = cur_safe = dwell_remaining_min
    return cur_prob + fut_prob, cur_safe + fut_safe


# --------------------------------------------------------------------------
# Plateau schedule file
# --------------------------------------------------------------------------
def open_plateaus_file(path, b):
    fh = open(path, "w")
    fh.write("# Plateau schedule for automated calibration run\n")
    fh.write(f"# gate_mode: {b['gate_mode']}\n")
    fh.write(f"# coarse settle: tol={b['tol']} C, window={b['window_min']} min, "
             f"timeout={b['timeout_per_10k']} min/10K (floor {b['timeout_floor']} min)\n")
    if b["gate_mode"] == "drift":
        g = b["gate"]
        fh.write(f"# drift gate: |drift|<{g['drift_mk']:g} mK and sd<{g['sd_mk']:g} mK "
                 f"over the trailing {g['window_min']:g} min on {g['channel'] or 'ref SPRT'}; "
                 f"min soak = plateau_minutes, cap = min soak + {g['max_extra_min']:g} min\n")
        fh.write("# columns: idx; setpoint_C; ramp_C_per_min; t_command; t_stable; "
                 "t_dwell_start; t_dwell_end; gate_ok; drift_mK; sd_mK; n\n")
        fh.write("# [t_dwell_start, t_dwell_end] is the accepted trailing average window; "
                 "average the reference/DUT over it. Row is written when the gate ACCEPTS "
                 "(end of soak); an interrupted soak leaves no row -- recover from raw logs.\n")
    else:
        fh.write("# columns: idx; setpoint_C; ramp_C_per_min; t_command; t_stable; "
                 "t_dwell_start; t_dwell_end; stable_ok\n")
        fh.write("# each row is written when its dwell STARTS (survives Ctrl-C); "
                 "t_dwell_end is the planned end = t_dwell_start + dwell.\n")
    fh.flush()
    return fh


def latest_sprt_temp(microk_file):
    """Read the last MicroK line from the log file and convert it to deg C.

    Decoupled from the logger thread on purpose: the worker keeps writing +
    flushing each reading, so we just tail the file. Returns (temp_C, channel)
    or None if nothing usable is there yet. MicroK line layout (see
    calibration_log.py): t_min; datetime; ratio; current; ChannelN; index
    """
    try:
        with open(microk_file, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 4096))     # last few lines are plenty
            tail = f.read().decode("utf-8", "replace")
        for line in reversed(tail.splitlines()):
            parts = [p.strip() for p in line.split(";")]
            if len(parts) >= 5:
                ratio = float(parts[2])
                channel = parts[4]
                return sprt.ratio_to_temp_c(ratio, channel), channel
    except Exception:
        pass
    return None


def sprt_status(microk_file):
    """One-line 'SPRT=... C' string for status displays (empty if no reading)."""
    res = latest_sprt_temp(microk_file)
    if res is None:
        return "SPRT=--.-- C"
    temp, channel = res
    return f"SPRT={temp:+.4f} C ({channel})"


def sprt_window_stats(microk_file, channel, window_min, max_bytes=262144):
    """Tail the MicroK log and return gate.window_stats() over the trailing window
    for the reference SPRT `channel` (None -> last-seen). None if not enough data.

    Decoupled from the logger thread (it keeps writing+flushing); we just read the
    tail. 256 KB comfortably covers a 30-min window at the MicroK's poll rate.
    """
    try:
        with open(microk_file, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            text = f.read().decode("utf-8", "replace")
        samples = gate.parse_sprt_samples(text, channel=channel or None)
        return gate.window_stats(samples, window_min)
    except Exception:
        return None


def gate_line(stats, g):
    """Compact 'drift/sd vs limits' string for status displays."""
    if not stats:
        return f"gate: SPRT settling (need {g['window_min']:.0f} min of data)"
    d_ok = "ok " if stats["drift_mk"] < g["drift_mk"] else "HI "
    s_ok = "ok " if stats["sd_mk"] < g["sd_mk"] else "HI "
    return (f"drift {stats['drift_mk']:5.1f} mK [{d_ok}<{g['drift_mk']:g}]   "
            f"sd {stats['sd_mk']:5.1f} mK [{s_ok}<{g['sd_mk']:g}]   "
            f"(n={stats['n']}, {stats['span_min']:.0f} min)")


def latest_ntc_temps(ntc_file, channels=ntc.NTC_CHANNELS):
    """Tail the NTC log and return ntc.ntc_from_row() output for the most recent
    measurement row -- [(node, [(channel, temp_C_or_None), ...]), ...] -- or None.

    Robust by design: the channel columns are located from the repeated HEADER line
    (which carries the `Nxx_<channel>` labels), so standalone columns and channel
    order are handled without assumptions. Rows are split as `Group; secs; datetime;
    datablock` -- a data row has a numeric 2nd field and no `NTC`/`New Node Array`
    text in its datablock (that filters out headers, echoes and meta lines).
    """
    try:
        with open(ntc_file, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 8192))
            lines = f.read().decode("utf-8", "replace").splitlines()

        data_idx = None
        for i in range(len(lines) - 1, -1, -1):
            parts = lines[i].split(";", 3)
            if len(parts) != 4 or not parts[0].strip().startswith("Group"):
                continue
            try:
                float(parts[1])
            except ValueError:
                continue                              # header row (secs = text)
            block = parts[3]
            if block.strip() and "NTC" not in block and "New Node Array" not in block:
                data_idx = i
                break
        if data_idx is None:
            return None

        dparts = lines[data_idx].split(";", 3)
        # dparts[3] may carry a trailing "; TOFFMS=..." per-value-timing field
        # (new token-accurate logger); the counts are everything before it.
        dgroup, dblock = dparts[0].strip(), dparts[3].split(";", 1)[0]
        header = None
        for i in range(data_idx, -1, -1):            # nearest preceding header, same group
            hp = lines[i].split(";", 3)
            if (len(hp) == 4 and hp[0].strip() == dgroup
                    and any(("_" + ch) in hp[3] for ch in channels)):
                header = hp[3]
                break
        if header is None:
            return None
        return ntc.ntc_from_row(header, dblock, channels=channels) or None
    except Exception:
        return None


def latest_ntc_temps_by_group(ntc_file, channels=ntc.NTC_CHANNELS):
    """Merge the most recent data row of EVERY group -> all nodes at once.

    The head multiplexes groups, so latest_ntc_temps() only ever shows the last
    group read. For the dashboard we want every node, so take each group's newest
    data row and concatenate. Returns [(node, [(channel, temp)...])...] or None.
    """
    try:
        with open(ntc_file, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 32768))            # enough for the last full cycle
            lines = f.read().decode("utf-8", "replace").splitlines()

        latest = {}                                  # group -> index of newest data row
        for i in range(len(lines) - 1, -1, -1):
            parts = lines[i].split(";", 3)
            if len(parts) != 4 or not parts[0].strip().startswith("Group"):
                continue
            grp = parts[0].strip()
            if grp in latest:
                continue
            try:
                float(parts[1])
            except ValueError:
                continue
            block = parts[3]
            if block.strip() and "NTC" not in block and "New Node Array" not in block:
                latest[grp] = i

        out = []
        for grp, didx in latest.items():
            # strip any trailing "; TOFFMS=..." per-value-timing field (new logger)
            dblock = lines[didx].split(";", 3)[3].split(";", 1)[0]
            header = None
            for i in range(didx, -1, -1):
                hp = lines[i].split(";", 3)
                if (len(hp) == 4 and hp[0].strip() == grp
                        and any(("_" + ch) in hp[3] for ch in channels)):
                    header = hp[3]
                    break
            if header is not None:
                out.extend(ntc.ntc_from_row(header, dblock, channels=channels))
        def node_num(r):
            try:
                return int(r[0][1:])
            except ValueError:
                return 0
        return sorted(out, key=node_num) or None
    except Exception:
        return None


def ntc_status(ntc_file, channels=ntc.NTC_CHANNELS):
    """Compact 'NTC[C] N94: NTC1=.. NTC2=.. TestSB=..' string for status displays;
    appends a 'Not connected' warning for any open channel."""
    res = latest_ntc_temps(ntc_file, channels=channels)
    if not res:
        return "NTC=--"
    return "NTC[C] " + ntc.format_ntc(res)


def interruptible_sleep(seconds, label, extra=None, on_tick=None):
    """Sleep in small steps so Ctrl-C stays responsive; print a coarse countdown.

    `extra` is an optional callable returning a status string to append.
    `on_tick(remaining_s)` is an optional callback; when given, it is called each
    step instead of printing the countdown line (used to drive the dashboard).
    """
    end = time.time() + seconds
    while True:
        remaining = end - time.time()
        if remaining <= 0:
            return
        if on_tick is not None:
            try:
                on_tick(remaining)
            except Exception:
                pass
        else:
            suffix = ""
            if extra is not None:
                try:
                    suffix = "   " + extra()
                except Exception:
                    suffix = ""
            print(f"  [{label}] {remaining/60:5.1f} min remaining ...{suffix}")
        time.sleep(min(15.0, remaining))


# --------------------------------------------------------------------------
# Live dashboard (opt-in) -- a fixed panel that refreshes in place instead of
# scrolling. All bath reads happen in the control thread that calls this, so
# there is no serial contention.
# --------------------------------------------------------------------------
def _safe(fn, fmt, default="--"):
    try:
        return fmt.format(fn())
    except Exception:
        return default


def render_dashboard(bath, ctx):
    def bath_read(name, fmt):
        m = getattr(bath, name, None)
        return _safe(m, fmt) if m else "--"

    elapsed = int(time.time() - ctx["t0"])
    hh, mm, ss = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
    L = []
    L.append("=" * 64)
    L.append(f" CALIBRATION  {ctx['exp']}")
    L.append(f" run {ctx['run_stamp']}    elapsed {hh:02d}:{mm:02d}:{ss:02d}"
             f"    (Ctrl-C to stop)")
    L.append("-" * 64)
    L.append(f" Plateau   {ctx['idx']}/{ctx['n']}    setpoint {ctx['setpoint']:+.2f} C"
             f"    ramp {ctx['ramp']}")
    if ctx["phase"] == "SETTLE":
        L.append(f" Phase     SETTLE  held {ctx['held']:.0f}/{ctx['window_s']:.0f} s"
                 f"    (timeout {ctx['timeout_min']:.0f} min)")
    elif ctx["phase"] == "GATE":
        L.append(f" Phase     GATE    soak {ctx['soak_min']:.0f} min"
                 f"   (min {ctx['min_soak']:.0f}, cap {ctx['max_soak']:.0f})")
        L.append(f"           {gate_line(ctx.get('gate'), ctx['gcfg'])}")
    else:
        note = "" if ctx.get("stable_ok", True) else "  [was NOT stable -> check]"
        L.append(f" Phase     DWELL   {ctx['dwell_remaining']/60:.1f} min left{note}")
    L.append(f" Bath      PV {bath_read('read_pv','{:+.3f}')} C   "
             f"SP {bath_read('read_setpoint','{:+.3f}')} C   "
             f"OUT {bath_read('read_output','{:.1f}')} %   "
             f"rate {bath_read('read_ramp_rate','{:g}')}")
    L.append(f" SPRT      {sprt_status(ctx['microk_file'])}")
    est = ctx.get("est")
    if est:
        if ctx["phase"] == "SETTLE":
            elapsed = (time.time() - ctx["plateau_start"]) / 60.0
            rp, rs = remaining_estimate(est, ctx["idx"], "SETTLE", elapsed, 0.0)
        elif ctx["phase"] == "GATE":
            rp, rs = remaining_estimate(est, ctx["idx"], "GATE", ctx["soak_min"], 0.0)
        else:
            rp, rs = remaining_estimate(est, ctx["idx"], "DWELL", 0.0,
                                        ctx.get("dwell_remaining", 0.0) / 60.0)
        now = time.time()
        L.append(f" ETA       probable ~{_clock(now + rp*60)} (~{_fmt_hm(rp)})    "
                 f"latest <= {_clock(now + rs*60)} (<= {_fmt_hm(rs)})")
    L.append("-" * 64)
    L.append(" NTC [C] (raw, mean-S4):")
    rows = latest_ntc_temps_by_group(ctx["logger_file"], channels=ctx["ntc_channels"])
    if rows:
        for node, chans in rows:
            cells = "  ".join(f"{ch}={t:+.3f}" if t is not None else f"{ch}=--"
                              for ch, t in chans)
            L.append(f"   {node:>4}: {cells}")
    else:
        L.append("   (waiting for NTC data ...)")
    L.append("=" * 64)
    # Home the cursor and clear to end of screen, then paint -- no scroll, low flicker.
    sys.stdout.write("\033[H\033[J" + "\n".join(L) + "\n")
    sys.stdout.flush()


# --------------------------------------------------------------------------
# dT/dt plateau gating (drift mode): soak until the SPRT reference is quiet
# --------------------------------------------------------------------------
def gate_plateau(bath, microk_file, gate_channel, g, min_soak_min, max_soak_min,
                 base_ctx, dash, say, tag):
    """Hold at the setpoint and soak until the reference SPRT stops moving.

    Gates on the SPRT (mK resolution) rather than the bath PV (10 mK): accept once
    the trailing-window drift and scatter are both under limit AND at least the
    per-plateau minimum soak has elapsed. A hard cap (max_soak_min) guarantees the
    plateau ends even if the reference keeps drifting (then it is flagged).

    Returns (gate_ok, t_accept, stats). The accepted measurement window is the
    trailing g['window_min'] minutes ending at t_accept.
    """
    start = time.time()
    last_stats = None
    while True:
        soak_min = (time.time() - start) / 60.0
        stats = sprt_window_stats(microk_file, gate_channel, g["window_min"])
        if stats:
            last_stats = stats
        passed = gate.gate_ok(stats, g["window_min"], g["drift_mk"], g["sd_mk"])
        if passed and soak_min >= min_soak_min:
            return True, datetime.now(), stats
        if soak_min >= max_soak_min:
            return False, datetime.now(), last_stats
        if dash:
            render_dashboard(bath, dict(base_ctx, phase="GATE", soak_min=soak_min,
                                        min_soak=min_soak_min, max_soak=max_soak_min,
                                        gate=stats, gcfg=g))
        else:
            say(f"  [{tag}] soak {soak_min:4.0f} min   {gate_line(stats, g)}")
        # Sleep one poll interval; keep Ctrl-C responsive, no countdown spam.
        interruptible_sleep(g["poll_s"], f"{tag} gate", on_tick=lambda rem: None)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Automated bath-driven calibration run")
    ap.add_argument("--param", default="param_combined.txt", help="path to the parameter file")
    ap.add_argument("--exp", default=None, help="override the experiment name")
    ap.add_argument("--dry-run", action="store_true",
                    help="connect and read the bath but never change its setpoint")
    ap.add_argument("--dashboard", action="store_true",
                    help="show a fixed, in-place overview panel instead of scrolling "
                         "log lines (loggers still write their files)")
    args = ap.parse_args()

    c = read_config(args.param, exp_override=args.exp)
    b = read_bath_config(args.param)

    # Gate on the reference SPRT by default (the first configured SPRT channel).
    if b["gate_mode"] == "drift" and not b["gate"]["channel"] and c["sprt_chs"]:
        b["gate"]["channel"] = "Channel" + c["sprt_chs"][0]

    # Which readout channels are NTC thermistors -> live temperature per node.
    # Keeps the config's order; non-NTC readouts (TempADC/GND/...) are not shown.
    ntc_channels = [ch for ch in c["active"] if ch in ntc.NTC_CHANNELS] or list(ntc.NTC_CHANNELS)

    print("Experiment :", c["exp"])
    print("MicroK     : Ref", c["ref_ch"], "| SPRT", c["sprt_chs"], "| hint", c["microk_hint"])
    print("TempHead   : readout", c["active"], "| nodes", c["Logger_sensorNo"], "| hint", c["logger_hint"])
    print("NTC->T     : converting channels", ntc_channels)
    enc_desc = "float" if b["use_float"] else f"int{b['decimals']}"
    print("Bath       : protocol", b["protocol"], "| address", b["address"],
          ("| encoding " + enc_desc if b["protocol"] == "modbus" else ""),
          "| hint", b["port_hint"])
    if b["gate_mode"] == "drift":
        g = b["gate"]
        print(f"Gate       : drift <{g['drift_mk']:g} mK & sd <{g['sd_mk']:g} mK over "
              f"{g['window_min']:g} min on {g['channel'] or 'ref SPRT'}"
              f"  (min soak = plateau_minutes, cap +{g['max_extra_min']:g} min)")
    else:
        print("Gate       : fixed-time dwell")
    soak_word = "min soak" if b["gate_mode"] == "drift" else "dwell"
    print("Plateaus   :")
    for i, (sp, mn, rp) in enumerate(zip(b["plateaus"], b["minutes"], b["ramps"]), 1):
        ramp = "max speed" if rp is None else f"{rp} C/min"
        print(f"   {i:2d}. {sp:+7.2f} C   {soak_word} {mn:g} min   ramp {ramp}")

    description = input("\nDescription of this calibration (press Enter to confirm): ").strip()

    # Select ports interactively (same helper as the legacy tool).
    microk_port = pick_port("MicroK bridge (9600 baud)", c["microk_hint"])
    logger_port = pick_port("SchwaRTech/AWI Temperature head (19200 baud)", c["logger_hint"])
    bath_port   = pick_port(f"Isotech bath controller ({b['protocol']})", b["port_hint"])
    print("\nSelected  MicroK   ->", microk_port)
    print("Selected  TempHead ->", logger_port)
    print("Selected  Bath     ->", bath_port)

    # File names (same convention as calibration_log.py, plus a plateau file).
    os.makedirs("Output", exist_ok=True)
    run_stamp    = time.strftime("%Y%m%d-%H%M%S")
    microk_file  = f"Output/{c['exp']}_{run_stamp}_microk.txt"
    logger_file  = f"Output/{c['exp']}_{run_stamp}_ntc.txt"
    meta_file    = f"Output/{c['exp']}_{run_stamp}_meta.txt"
    plateau_file = f"Output/{c['exp']}_{run_stamp}_plateaus.txt"

    write_meta(meta_file, args.param, c, microk_port, logger_port, microk_file, logger_file,
               run_stamp, description)
    with open(meta_file, "a") as m:
        m.write("\n--- Bath automation ---\n")
        m.write(f"Bath port        : {bath_port}\n")
        m.write(f"Bath protocol    : {b['protocol']}\n")
        m.write(f"Bath address     : {b['address']}\n")
        if b["protocol"] == "modbus":
            m.write(f"Bath encoding    : {'float' if b['use_float'] else 'int'+str(b['decimals'])}\n")
        m.write(f"Plateaus (C)     : {b['plateaus']}\n")
        soak_lbl = "Min soak (min)   " if b["gate_mode"] == "drift" else "Dwell (min)      "
        m.write(f"{soak_lbl}: {b['minutes']}\n")
        m.write(f"Ramps (C/min)    : {['max' if r is None else r for r in b['ramps']]}\n")
        m.write(f"Coarse settle    : tol={b['tol']} C, window={b['window_min']} min, "
                f"timeout={b['timeout_per_10k']} min/10K (floor {b['timeout_floor']} min)\n")
        m.write(f"Gate mode        : {b['gate_mode']}\n")
        if b["gate_mode"] == "drift":
            g = b["gate"]
            m.write(f"Gate criteria    : |drift|<{g['drift_mk']:g} mK & sd<{g['sd_mk']:g} mK "
                    f"over {g['window_min']:g} min on {g['channel'] or 'ref SPRT'}\n")
            m.write(f"Gate soak        : min = plateau_minutes, cap = min + "
                    f"{g['max_extra_min']:g} min, poll {g['poll_s']:g} s\n")
        m.write(f"Plateau file     : {plateau_file}\n")
    print("Meta file written:", meta_file)

    # --- Connect to the bath first: this is the smoke test for wiring/baud/
    #     slave/encoding. Abort *before* starting the loggers if it fails. ---
    try:
        bath = make_bath(bath_port, b)
        rate_str = ""
        if hasattr(bath, "read_ramp_rate"):
            rr = bath.read_ramp_rate()
            rate_str = f"  RAMP={'off' if rr == 0 else f'{rr:g} C/min'}"
        pv0 = bath.read_pv()
        print(f"\nBath connected ({b['protocol']}). PV={pv0:+.4f} C  "
              f"SP={bath.read_setpoint():+.4f} C  OUT={bath.read_output():.1f} %{rate_str}")
    except Exception as e:
        sys.exit(
            f"\nCould not talk to the bath controller: {e}\n"
            "Check: RS232 cable/converter, the bath_protocol (bisynch vs modbus), "
            "the address, and — for modbus — baud/parity and value encoding."
        )

    # Time estimate for the whole schedule (from the current bath temperature).
    est = estimate_schedule(b, pv0)
    tp = sum(r["probable"] for r in est)
    ts = sum(r["safe"] for r in est)
    now = time.time()
    print(f"\nEstimated run time ({len(est)} plateaus, from PV {pv0:+.1f} C):")
    print(f"   probable ~{_fmt_hm(tp)}   -> finish ~{_clock(now + tp * 60)}")
    print(f"   latest  <= {_fmt_hm(ts)}   -> finish <= {_clock(now + ts * 60)}")
    print("   (latest is the hard bound from the stability timeouts + dwell.)")

    if args.dry_run:
        print("\n--dry-run: not moving the bath. Exiting.")
        return

    # --- Start the two loggers (continuous, for the whole run). ---
    stop_event = threading.Event()
    t_micro  = threading.Thread(target=microk_worker,
                                args=(stop_event, c, microk_port, microk_file, args.dashboard),
                                daemon=True, name="MicroK")
    t_logger = threading.Thread(target=logger_worker,
                                args=(stop_event, c, logger_port, logger_file, args.dashboard),
                                daemon=True, name="TempHead")
    t_micro.start()
    t_logger.start()
    print("\nLoggers running. Starting plateau schedule. Stop anytime with Ctrl-C.\n")

    pf = open_plateaus_file(plateau_file, b)
    n_plateaus = len(b["plateaus"])
    run_t0 = time.time()
    dash = args.dashboard
    def say(*a):
        if not dash:
            print(*a)
    try:
        for i, (sp, minutes, ramp) in enumerate(zip(b["plateaus"], b["minutes"], b["ramps"]), 1):
            tag = f"Plateau {i}/{n_plateaus}"        # progress marker shown everywhere
            ramp_str = "max" if ramp is None else f"{ramp:g} C/min"
            say(f"\n===== {tag}: setpoint {sp:+.3f} C "
                f"({soak_word} {minutes:g} min, ramp {ramp_str}) =====")

            # Timeout scales with how far the bath must travel from where it is
            # right now (30 min/10 K by default). If it still hasn't settled by
            # then, we measure anyway rather than block the whole run.
            pv_now  = bath.read_pv()
            timeout = plateau_timeout_min(b, sp - pv_now)
            say(f"  step {pv_now:+.2f} -> {sp:+.2f} C (|dT|={abs(sp-pv_now):.1f} K); "
                f"stability timeout {timeout:.0f} min.")

            bath.set_ramp_rate(ramp)          # None/0 -> rate limit off (max speed)
            bath.set_setpoint(sp)
            t_command = datetime.now()
            plateau_t0 = time.time()

            # Remaining-time estimate at the start of this plateau (SETTLE phase).
            rp, rs = remaining_estimate(est, i, "SETTLE", 0.0, 0.0)
            say(f"  ETA: probable ~{_clock(time.time() + rp*60)} (~{_fmt_hm(rp)} left)"
                f"  |  latest <= {_clock(time.time() + rs*60)} (<= {_fmt_hm(rs)} left)")

            # Shared dashboard context for this plateau (used only when --dashboard).
            base = dict(exp=c["exp"], run_stamp=run_stamp, t0=run_t0, idx=i,
                        n=n_plateaus, setpoint=sp, ramp=ramp_str, timeout_min=timeout,
                        window_s=b["window_min"] * 60, microk_file=microk_file,
                        logger_file=logger_file, ntc_channels=ntc_channels,
                        est=est, plateau_start=plateau_t0)
            on_poll = None
            if dash:
                on_poll = lambda pv, inb, held: render_dashboard(
                    bath, dict(base, phase="SETTLE", held=held))
                render_dashboard(bath, dict(base, phase="SETTLE", held=0.0))

            # Live SPRT reading (from the MicroK log), tagged with the plateau
            # progress so you always see where you are while waiting to settle.
            status = lambda: f"[{tag}] {sprt_status(microk_file)}"

            ok = bath.wait_until_stable(
                sp, tol=b["tol"],
                window_s=b["window_min"] * 60,
                poll_s=5.0,
                timeout_s=timeout * 60,
                verbose=not dash,
                extra=status,
                on_poll=on_poll,
            )
            t_stable = datetime.now()          # bath PV reached the setpoint band (coarse)

            if b["gate_mode"] == "drift":
                # --- dT/dt gate: soak until the SPRT reference stops moving ---
                g = b["gate"]
                min_soak, max_soak = minutes, minutes + g["max_extra_min"]
                if ok:
                    say(f"  -> {tag} at setpoint. Soaking until the SPRT is quiet "
                        f"(min {min_soak:g} min, cap {max_soak:g} min).")
                else:
                    say(f"  !! {tag} bath not in band within {timeout:.0f} min; "
                        f"soaking on the SPRT gate anyway.")
                gate_pass, t_accept, gstats = gate_plateau(
                    bath, microk_file, g["channel"], g,
                    min_soak, max_soak, base, dash, say, tag)
                # Accepted measurement window = the trailing gate window ending at accept.
                t_dwell_end   = t_accept
                t_dwell_start = t_accept - timedelta(minutes=g["window_min"])
                if gate_pass:
                    say(f"  -> {tag} gate passed after "
                        f"{(t_accept - t_stable).total_seconds()/60:.0f} min. "
                        f"Window = trailing {g['window_min']:g} min "
                        f"[{t_dwell_start:%H:%M}..{t_dwell_end:%H:%M}].")
                else:
                    say(f"  !! {tag} soak cap hit without the gate passing -- window "
                        f"recorded but flagged (check the reference drift).")
                d_s = f"{gstats['drift_mk']:.2f}" if gstats else "NA"
                s_s = f"{gstats['sd_mk']:.2f}"    if gstats else "NA"
                n_s = gstats["n"] if gstats else 0
                # Row written when the gate ACCEPTS (end of soak); extra QC columns.
                pf.write(f"{i}; {sp}; {'off' if ramp is None else ramp}; "
                         f"{t_command}; {t_stable}; {t_dwell_start}; {t_dwell_end}; "
                         f"{gate_pass}; {d_s}; {s_s}; {n_s}\n")
                pf.flush()
                say(f"  {tag} done.")
            else:
                # --- legacy fixed-time dwell ---
                if ok:
                    say(f"  -> {tag} stable at {sp:+.3f} C. Measuring for {minutes:g} min.")
                else:
                    say(f"  !! {tag} NOT stable within {timeout:.0f} min. "
                        f"Measuring anyway -- check this plateau afterwards.")

                t_dwell_start = datetime.now()
                t_dwell_end   = t_dwell_start + timedelta(minutes=minutes)  # planned end
                # Write the plateau row NOW, at the START of the measurement window, and
                # flush -- so the window is on disk immediately and survives a Ctrl-C
                # during a long dwell (t_dwell_end is the planned end = start + dwell;
                # for a completed plateau it matches the actual end within milliseconds).
                pf.write(f"{i}; {sp}; {'off' if ramp is None else ramp}; "
                         f"{t_command}; {t_stable}; {t_dwell_start}; {t_dwell_end}; {ok}\n")
                pf.flush()

                # During the dwell, show bath PV/SP, the SPRT temperature, and the raw
                # NTC temperature per node for every configured NTC channel.
                if dash:
                    on_tick = lambda rem: render_dashboard(
                        bath, dict(base, phase="DWELL", dwell_remaining=rem, stable_ok=ok))
                    interruptible_sleep(minutes * 60, f"{tag} dwell", on_tick=on_tick)
                else:
                    dwell_status = lambda: (f"bath PV={bath.read_pv():+.3f} SP={sp:+.3f} | "
                                            f"{sprt_status(microk_file)} | "
                                            f"{ntc_status(logger_file, ntc_channels)}")
                    interruptible_sleep(minutes * 60, f"{tag} dwell", extra=dwell_status)
                say(f"  {tag} done.")

        if dash:
            sys.stdout.write("\033[H\033[J")     # leave a clean screen
        print("\nAll plateaus complete. Stopping loggers ...")
    except KeyboardInterrupt:
        print("\nInterrupted. Stopping loggers ...")
    finally:
        stop_event.set()
        t_micro.join(timeout=15)
        t_logger.join(timeout=15)
        pf.close()
        print("Done. Files are in ./Output/  (bath left at its last setpoint).")


if __name__ == "__main__":
    main()
