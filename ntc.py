#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NTC raw ADC counts -> resistance -> temperature, for live display only.

Ported from CalibrationChains/lib/SPRTRtoT_NTCtoR.R (NTCcounts2temp / NTCcounts2R).
This is the *raw* (nominal) conversion: it uses generic Steinhart-Hart-type
constants (beta = 3380 K, R25 = 10 kOhm at 25 C), NOT the per-sensor calibration.
It is meant only to show a rough temperature per node on screen during a run so
the operator can see roughly where the NTCs sit relative to the bath and SPRT.
The authoritative per-sensor calibration is done later in the R pipeline.
"""

import math

ADC_FULLSCALE = 33554432        # 2**25, from the head's ADC front-end
DIVIDER_OHM   = 499000.0        # fixed divider resistor in the front-end
BETA          = 3380.0          # nominal NTC beta [K]
R25           = 10000.0         # nominal NTC resistance at 25 C [Ohm]
T25           = 298.15          # 25 C in Kelvin

# Raw counts above this mean an open input -> the node/sensor is not connected.
DISCONNECTED_COUNTS = 10_000_000


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


def counts_to_temp_c(counts, beta=BETA, r25=R25, t25=T25):
    """Raw ADC counts -> temperature in deg C (or None if not computable)."""
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


if __name__ == "__main__":
    # Sanity check against real recorded counts (bath was near -46 C).
    for c in (5812827, 5540969, 4952001, 4540193):
        r = counts_to_resistance(c)
        print(f"counts={c} -> R={r:9.1f} ohm -> {counts_to_temp_c(c):+7.3f} C")
