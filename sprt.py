#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SPRT resistance-ratio (W = Rt/Rtpw) -> temperature, for live display only.

Ported from CalibrationChains/lib/SPRTRtoT_NTCtoR.R. The MicroK bridge reports a
dimensionless ratio W; this converts it to degrees Celsius using the same simple
two-point LINEAR calibration the R workflow uses -- anchored on the mercury point
and the water triple point:

    T = m*W + b        with m, b from the two fixed points

This is an approximation (no ITS-90 deviation function) and is meant only to give
the operator a readable temperature next to the bath setpoint/PV during a run.
The authoritative calibration is still done in the R pipeline.

IMPORTANT: the ratios below are the H1-2025 SPRT calibration values from the R
code. Update them (or load from a file) whenever the SPRTs are recalibrated --
keep them in sync with SPRTRtoT_NTCtoR.R.
"""

# Fixed-point temperatures (Kelvin)
_T_TWP  = 0.01     + 273.15    # water triple point
_T_MERC = -38.8344 + 273.15    # mercury freezing point

# Glass SPRT ratios at the two fixed points (H1 2025)
_GLAS_TWP  = 0.254210687
_GLAS_MERC = 0.214602392

# 670SL SPRT ratios at the two fixed points (H1 2025)
_SL670_TWP  = 0.256828407
_SL670_MERC = 0.216812093


def _linear_kelvin(w, w_merc, w_twp):
    m = (_T_TWP - _T_MERC) / (w_twp - w_merc)
    b = _T_TWP - m * w_twp
    return m * w + b


def ratio_to_temp_glas_c(w):
    """Glass SPRT: ratio W -> temperature in deg C."""
    return _linear_kelvin(w, _GLAS_MERC, _GLAS_TWP) - 273.15


def ratio_to_temp_670sl_c(w):
    """670SL SPRT: ratio W -> temperature in deg C."""
    return _linear_kelvin(w, _SL670_MERC, _SL670_TWP) - 273.15


def ratio_to_temp_c(w, channel=None):
    """Convert a MicroK ratio to deg C, picking the SPRT by channel label.

    Follows the R workflow's channel convention:
        Channel2 -> glass SPRT     Channel3 -> 670SL SPRT
    Anything else falls back to the glass SPRT.
    """
    ch = (channel or "").replace("Channel", "").strip()
    if ch == "3":
        return ratio_to_temp_670sl_c(w)
    return ratio_to_temp_glas_c(w)


if __name__ == "__main__":
    # Sanity check: the two anchor ratios must map back to the fixed points.
    for w, ch, want in [(_GLAS_TWP, "Channel2", 0.01),
                        (_GLAS_MERC, "Channel2", -38.8344),
                        (_SL670_TWP, "Channel3", 0.01),
                        (_SL670_MERC, "Channel3", -38.8344)]:
        got = ratio_to_temp_c(w, ch)
        print(f"W={w:.9f} {ch}: {got:+.4f} C  (expect {want:+.4f})")
