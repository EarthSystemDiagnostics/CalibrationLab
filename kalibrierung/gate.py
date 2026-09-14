#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Plateau gating on the SPRT reference: is the bath quiet enough to measure?

Empirical finding from the recalib runs: a plateau is "done" not after a fixed
number of minutes but when the *reference thermometer* stops moving. Slow creep
(a few mK over half an hour) does no harm to the fit; a live dT/dt does. So we
gate each plateau on the SPRT (MicroK), which resolves ~sub-mK -- the bath's own
PV only reads to 0.01 C (10 mK) and cannot see a 3 mK drift at all.

Core rule (defaults):
    accept the measurement window when, over the trailing `window_min` minutes,
        |drift|  <  ~3 mK   (linear-fit slope * window)
        sd       <  ~8 mK   (scatter about that fit line)
    then average the reference/DUT over that same trailing window.

This module is deliberately pure and file-format-only so it can be unit-tested
without hardware:  parse_sprt_samples(text) -> [(t_min, temp_C), ...]  and
window_stats(samples, window_min) -> drift/sd summary.  calibration_auto.py tails
the live MicroK log and feeds the text in.
"""

import sprt


def parse_sprt_samples(text, channel=None):
    """Parse (t_min, temp_C) pairs from MicroK log text for ONE SPRT channel.

    MicroK line layout (see calibration_log.microk_worker):
        t_min ; datetime ; ratio ; current ; ChannelN ; index
    We use field 0 (minutes since the logger started -- a clean monotonic clock)
    as the time axis and convert the ratio (field 2) with sprt.ratio_to_temp_c.

    channel:
        None      -> lock onto the channel of the LAST data line (the reference
                     SPRT that the display is following) and keep only those rows.
        "Channel2"/"2" -> keep only that channel.
    Rows for other channels are skipped so the trailing window is single-channel
    (mixing channels would corrupt drift/sd). Returns a list sorted by t_min.
    """
    rows = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(";")]
        if len(parts) < 6:
            continue
        try:
            t_min = float(parts[0])
            ratio = float(parts[2])
        except ValueError:
            continue
        ch = parts[4]
        rows.append((t_min, ratio, ch))
    if not rows:
        return []

    want = channel
    if want is None:
        want = rows[-1][2]
    want = str(want).replace("Channel", "").strip()

    out = []
    for t_min, ratio, ch in rows:
        if ch.replace("Channel", "").strip() != want:
            continue
        out.append((t_min, sprt.ratio_to_temp_c(ratio, ch)))
    out.sort(key=lambda r: r[0])
    return out


def _linfit(xs, ys):
    """Ordinary least-squares slope/intercept of ys ~ xs. xs must not be constant."""
    n = len(xs)
    sx = sum(xs); sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if denom == 0:
        return 0.0, sy / n
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    return slope, intercept


def window_stats(samples, window_min):
    """Drift/sd of the trailing `window_min` minutes of `samples`.

    samples: [(t_min, temp_C), ...] sorted ascending (parse_sprt_samples output).
    Returns None if there is not yet enough data, else a dict:
        n            number of points in the window
        span_min     time actually covered (last - first) [min]
        slope_mk_min signed drift rate [mK/min]
        drift_mk     |slope| * window_min  -> drift over a full window [mK]
        sd_mk        sd of residuals about the fit line (noise) [mK]
        mean_c       mean temperature over the window [deg C]
        t_last       t_min of the most recent sample
    Drift and noise are separated on purpose: we fit a line, so `drift_mk` is the
    trend and `sd_mk` is the scatter left after removing it -- gating on both means
    "not moving AND not noisy", which raw sd alone would conflate.
    """
    if not samples:
        return None
    t_last = samples[-1][0]
    win = [(t, x) for (t, x) in samples if t >= t_last - window_min]
    n = len(win)
    if n < 3:
        return None
    ts = [t for t, _ in win]
    mk = [x * 1000.0 for _, x in win]          # work in mK
    span = ts[-1] - ts[0]
    slope, intercept = _linfit(ts, mk)
    resid = [y - (slope * t + intercept) for t, y in zip(ts, mk)]
    dof = max(n - 2, 1)                         # 2 fit params
    sd = (sum(r * r for r in resid) / dof) ** 0.5
    return {
        "n": n,
        "span_min": span,
        "slope_mk_min": slope,
        "drift_mk": abs(slope) * window_min,
        "sd_mk": sd,
        "mean_c": (sum(mk) / n) / 1000.0,
        "t_last": t_last,
    }


def gate_ok(stats, window_min, drift_mk, sd_mk, coverage=0.9):
    """True if `stats` (from window_stats) passes the drift+sd gate.

    Requires the window to be reasonably full (span >= coverage*window) so we
    don't accept on a couple of minutes of data, plus |drift| and sd under limits.
    """
    if not stats:
        return False
    return (stats["span_min"] >= coverage * window_min
            and stats["drift_mk"] < drift_mk
            and stats["sd_mk"] < sd_mk)
