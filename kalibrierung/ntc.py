#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NTC raw ADC counts -> resistance -> temperature, for live display only.

counts -> resistance is ported from CalibrationChains/lib/SPRTRtoT_NTCtoR.R
(NTCcounts2R). resistance -> temperature uses the universal MEAN lab calibration
(a 4-parameter Steinhart-Hart curve, NTC_MEAN_COEF below), NOT a per-sensor fit:
it is the mean of the 62 healthy GRIP sensors and represents every healthy sensor
to +/-0.03..0.05 C over -40..0 C. It replaces the old nominal Beta curve (beta=3380),
which read systematically too warm (+0.7 C at 0 C, growing to +3.6 C at -40 C).

Still display-only / not a per-sensor calibration -- the authoritative per-sensor
fit is applied later in the R pipeline -- but far closer to truth than Beta so the
operator sees a realistic temperature per node during a run. The Beta curve is kept
as counts_to_temp_c_beta() for the lab-vs-Beta diagnostic only.
"""

import math

ADC_FULLSCALE = 33554432        # 2**25, from the head's ADC front-end
DIVIDER_OHM   = 499000.0        # fixed divider resistor in the front-end

# Universal MEAN lab-S4 calibration: 1/T[K] = a + b*lnR + c*lnR^2 + d*lnR^3,
# T_C = 1/(1/T) - 273.15, with R in ohms. Mean of the 62 healthy GRIP sensors
# (a_suspect fits dropped). Source of record (EncoderHeads2026 repo):
#   meta/mean_calibration_coefficients.csv        -- machine-readable, one row
#   Greenland2026/derive_mean_calibration.R       -- reproducible derivation
# per-NTC means differ by <=0.008 C, so ONE global set is used for all channels.
NTC_MEAN_COEF = (
    8.4229499262e-04,     # a
    2.7615486685e-04,     # b
    -3.1654916185e-06,    # c
    3.0727486494e-07,     # d
)

# Nominal Beta model constants -- kept ONLY for the counts_to_temp_c_beta()
# diagnostic; no longer the default conversion.
BETA          = 3380.0          # nominal NTC beta [K]
R25           = 10000.0         # nominal NTC resistance at 25 C [Ohm]
T25           = 298.15          # 25 C in Kelvin

# Raw counts above this mean an open input -> the node/sensor is not connected.
DISCONNECTED_COUNTS = 10_000_000

# The head channels that are actually NTC thermistors and can be converted to a
# temperature with the formula above. TestSB is wired as a second NTC (NTC2), so
# it converts the same way. TempADC/TestN/GND/PRESSURE are NOT thermistors and
# are never temperature-converted.
NTC_CHANNELS = ("NTC1", "NTC2", "TestSB")


def is_connected(counts):
    """True if the raw counts look like a real, connected sensor."""
    return counts is not None and counts <= DISCONNECTED_COUNTS


def counts_to_resistance(counts):
    """Raw ADC counts -> NTC resistance in Ohm (or None if out of range)."""
    try:
        denom = counts - ADC_FULLSCALE
        if denom == 0:
            return None
        h = (-counts * 1_000_000.0) / denom
        if (DIVIDER_OHM - h) == 0:
            return None
        r = (h * DIVIDER_OHM) / (DIVIDER_OHM - h)
        return r if r > 0 else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def resistance_to_temp_c(r, coef=NTC_MEAN_COEF):
    """NTC resistance [Ohm] -> temperature [deg C] via the 4-param S4 model
    (1/T = a + b*lnR + c*lnR^2 + d*lnR^3). Returns None if not computable."""
    if r is None or r <= 0:
        return None
    try:
        a, b, c, d = coef
        ln = math.log(r)
        inv_t = a + b * ln + c * ln * ln + d * ln * ln * ln
        if inv_t == 0:
            return None
        return 1.0 / inv_t - 273.15
    except (ValueError, ZeroDivisionError):
        return None


def counts_to_temp_c(counts, coef=NTC_MEAN_COEF):
    """Raw ADC counts -> temperature in deg C using the mean lab-S4 calibration
    (or None if not computable). This is the default on-screen conversion."""
    return resistance_to_temp_c(counts_to_resistance(counts), coef=coef)


def counts_to_temp_c_beta(counts, beta=BETA, r25=R25, t25=T25):
    """Raw ADC counts -> temperature via the nominal Beta model. DIAGNOSTIC ONLY --
    reads systematically too warm; use counts_to_temp_c() for the real display."""
    r = counts_to_resistance(counts)
    if r is None:
        return None
    try:
        inv_t = (1.0 / beta) * math.log(r / r25) + 1.0 / t25
        if inv_t == 0:
            return None
        return 1.0 / inv_t - 273.15
    except (ValueError, ZeroDivisionError):
        return None


def _cells(block):
    """Split an NTC header/data block into segments (by '||') of cells (by '|')."""
    return [[c.strip() for c in seg.split("|")] for seg in block.split("||")]


def ntc1_from_row(header_cols, data_block):
    """Extract [(node_label, raw_temp_C), ...] for NTC1 of each node in one row.

    `header_cols`  -- the '||'-joined column labels (the part after
                      "SecondsElapsed; DateTimePC; " in a group header).
    `data_block`   -- the matching '||'-joined data values (raw ADC counts).

    Returns [(node_label, temp_C), ...]; `temp_C` is None for a node whose raw
    counts exceed DISCONNECTED_COUNTS (open input -> not connected). NTC1 columns
    are located by their `Nxx_NTC1` label, so standalone columns and channel order
    need no assumptions. Non-data blocks (echoed headers, meta lines) yield an
    empty list. Returns [] if header and data don't line up.
    """
    hsegs, dsegs = _cells(header_cols), _cells(data_block)
    if len(hsegs) != len(dsegs):
        return []
    out = []
    for hseg, dseg in zip(hsegs, dsegs):
        for j, label in enumerate(hseg):
            if label.endswith("_NTC1") and j < len(dseg):
                node = label[:-len("_NTC1")]
                try:
                    counts = float(dseg[j])
                except ValueError:
                    continue
                if not is_connected(counts):
                    out.append((node, None))          # sensor not connected
                else:
                    t = counts_to_temp_c(counts)
                    if t is not None:
                        out.append((node, t))
    return out


def format_ntc1(pairs):
    """Format ntc1_from_row() output for a status line: connected nodes as
    'N90=+.. ', plus a '!! Nodes not connected: ...' warning for None entries."""
    connected = " ".join(f"{node}={t:+.3f}" for node, t in pairs if t is not None)
    disconnected = [node for node, t in pairs if t is None]
    parts = [connected] if connected else []
    if disconnected:
        parts.append("!! Nodes not connected: " + " ".join(disconnected))
    return "  ".join(parts)


def ntc_from_row(header_cols, data_block, channels=NTC_CHANNELS):
    """Like ntc1_from_row but for SEVERAL NTC channels per node.

    Returns [(node_label, [(channel, temp_C_or_None), ...]), ...] in header order.
    For each node the requested `channels` (default NTC1/NTC2/TestSB) are located
    by their `Nxx_<channel>` label and converted to temperature. A channel whose
    raw counts exceed DISCONNECTED_COUNTS yields temp None (flagged as not
    connected by format_ntc); a connected-but-uncomputable channel is omitted.
    Returns [] if header and data don't line up.
    """
    hsegs, dsegs = _cells(header_cols), _cells(data_block)
    if len(hsegs) != len(dsegs):
        return []
    out = []
    for hseg, dseg in zip(hsegs, dsegs):
        node_map, node_order = {}, []
        for j, label in enumerate(hseg):
            for ch in channels:
                if label.endswith("_" + ch) and j < len(dseg):
                    node = label[:-len("_" + ch)]
                    if node not in node_map:
                        node_map[node], _ = {}, node_order.append(node)
                    try:
                        counts = float(dseg[j])
                    except ValueError:
                        break
                    if not is_connected(counts):
                        node_map[node][ch] = None            # not connected
                    else:
                        t = counts_to_temp_c(counts)
                        if t is not None:
                            node_map[node][ch] = t
                    break
        for node in node_order:
            chans = [(ch, node_map[node][ch]) for ch in channels if ch in node_map[node]]
            out.append((node, chans))
    return out


def format_ntc(rows):
    """Format ntc_from_row() output for a status line, all channels per node:
    'N94: NTC1=-13.0 NTC2=-13.0 TestSB=-12.9   N96: ...' plus a
    '!! Not connected: N94/TestSB ...' warning for any open channel."""
    parts, disconnected = [], []
    for node, chans in rows:
        vals = []
        for ch, t in chans:
            if t is None:
                disconnected.append(f"{node}/{ch}")
            else:
                vals.append(f"{ch}={t:+.3f}")
        if vals:
            parts.append(f"{node}: " + " ".join(vals))
    line = "   ".join(parts)
    if disconnected:
        line += ("   " if line else "") + "!! Not connected: " + " ".join(disconnected)
    return line


if __name__ == "__main__":
    # Sanity check against real recorded counts; show the mean-S4 default next to
    # the old Beta value (Beta reads systematically too warm).
    print(f"{'counts':>9}  {'R [ohm]':>10}  {'S4_mean':>9}  {'Beta':>9}  {'diff':>7}")
    for cnt in (5812827, 5540969, 4952001, 4540193):
        r = counts_to_resistance(cnt)
        s4, bt = counts_to_temp_c(cnt), counts_to_temp_c_beta(cnt)
        print(f"{cnt:9d}  {r:10.1f}  {s4:+9.3f}  {bt:+9.3f}  {s4-bt:+7.3f}")
