#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Offline test suite for the bath-control stack -- no hardware, no dependencies.

Run:   python3 test_bath.py            (also works under pytest)

It stubs out pyserial and minimalmodbus and replaces the Modbus Instrument with a
FakeEurotherm slave that faithfully models the two things that actually matter for
protocol correctness on a Eurotherm Series 2000:

  1. Scaled-integer registers WITH a configurable controller decimal-place setting,
     so a decimals mismatch produces the real factor-of-10 error.
  2. The separate IEEE-float region at (2*native + 0x8000), so float access is only
     served at the offset address -- reading the native address as a float fails,
     which is exactly the bug this catches.

On top of that it simulates the bath physics (PV converging toward the working
setpoint, optional steady-state offset, setpoint ramping, write rejection) so the
higher-level logic -- set/verify, wait_until_stable, the scaled timeout, and the
plateau schedule -- can be exercised end to end.
"""

import sys
import types
import time


# --------------------------------------------------------------------------
# Stub pyserial + minimalmodbus BEFORE importing the modules under test
# --------------------------------------------------------------------------
_serial = types.ModuleType("serial")
_tools  = types.ModuleType("serial.tools")
_lp     = types.ModuleType("serial.tools.list_ports")
_lp.comports = lambda: []
_serial.tools = _tools
_tools.list_ports = _lp
_serial.EIGHTBITS = 8
_serial.SEVENBITS = 7
_serial.PARITY_NONE = "N"
_serial.PARITY_EVEN = "E"
_serial.PARITY_ODD = "O"
_serial.STOPBITS_ONE = 1
_serial.Serial = object
sys.modules.update({"serial": _serial,
                    "serial.tools": _tools,
                    "serial.tools.list_ports": _lp})

_mm = types.ModuleType("minimalmodbus")
_mm.MODE_RTU = "rtu"


class FakeEurotherm:
    """Minimal Eurotherm Series 2000 Modbus slave simulator.

    Configured from the module-global SIM dict at construction time (so tests can
    set up a scenario, then let Bath() build the instrument internally).
    """

    # native register -> physical attribute
    _REG = {1: "pv", 2: "target", 3: "output", 5: "wsp",
            24: "sp1", 25: "sp2", 35: "sp_rate"}
    _WRITABLE = {2, 24, 25, 35}

    def __init__(self, port, slave):
        cfg = dict(SIM)
        self.ctrl_decimals = cfg.get("ctrl_decimals", 1)   # controller's config
        self.offset        = cfg.get("offset", 0.0)        # steady-state PV error
        self.reject_writes = cfg.get("reject_writes", False)
        self.conv          = cfg.get("conv", 0.5)          # PV convergence fraction
        # physical state
        self.pv       = cfg.get("pv", 20.0)
        self.sp1      = cfg.get("sp1", 20.0)
        self.sp2      = 20.0
        self.target   = self.sp1
        self.wsp      = self.sp1                            # working SP starts at SP1
        self.output   = 0.0
        self.sp_rate  = 0.0                                 # deg/min; 0 = off
        self.serial   = types.SimpleNamespace()
        self.mode     = None
        self.clear_buffers_before_each_transaction = False

    # -- physics ---------------------------------------------------------
    def _step(self):
        selected = self.sp1                                # assume SP1 active
        if self.sp_rate > 0:
            step = self.sp_rate                            # 1 "minute" per read
            if abs(selected - self.wsp) <= step:
                self.wsp = selected
            else:
                self.wsp += step if selected > self.wsp else -step
        else:
            self.wsp = selected
        target_pv = self.wsp + self.offset
        self.pv += self.conv * (target_pv - self.pv)
        self.output = max(-100.0, min(100.0, (target_pv - self.pv) * 10.0))

    def _phys(self, reg):
        return getattr(self, self._REG[reg])

    # -- Modbus interface (subset used by bath.Bath) ---------------------
    def read_register(self, registeraddress, number_of_decimals=0,
                      functioncode=3, signed=False):
        if registeraddress == 1:
            self._step()
        raw = round(self._phys(registeraddress) * 10 ** self.ctrl_decimals)
        if signed:
            if not -32768 <= raw <= 32767:
                raise ValueError("int16 overflow in fake register")
        return raw / 10 ** number_of_decimals

    def write_register(self, registeraddress, value, number_of_decimals=0,
                       functioncode=16, signed=False):
        if registeraddress not in self._WRITABLE:
            raise ValueError(f"register {registeraddress} not writable")
        if self.reject_writes and registeraddress in (2, 24, 25):
            return                                         # program running: ignored
        raw = round(value * 10 ** number_of_decimals)
        setattr(self, self._REG[registeraddress], raw / 10 ** self.ctrl_decimals)

    def _native_from_float_addr(self, addr):
        if addr < 0x8000 or (addr - 0x8000) % 2 != 0:
            raise ValueError(f"float access at non-IEEE address {addr:#x}")
        return (addr - 0x8000) // 2

    def read_float(self, registeraddress, functioncode=3,
                   number_of_registers=2, byteorder=0):
        reg = self._native_from_float_addr(registeraddress)
        if reg == 1:
            self._step()
        return float(self._phys(reg))                      # full resolution

    def write_float(self, registeraddress, value, number_of_registers=2, byteorder=0):
        reg = self._native_from_float_addr(registeraddress)
        if reg not in self._WRITABLE:
            raise ValueError(f"register {reg} not writable")
        if self.reject_writes and reg in (2, 24, 25):
            return
        setattr(self, self._REG[reg], float(value))


_mm.Instrument = FakeEurotherm
sys.modules["minimalmodbus"] = _mm

# Module-global scenario config, reset by configure()
SIM = {}


def configure(**kw):
    global SIM
    SIM = kw


# Now safe to import the code under test
import bath                         # noqa: E402
import sprt                         # noqa: E402
import ntc                          # noqa: E402
import calibration_auto as ca       # noqa: E402


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------
def test_encoding_map():
    assert bath._encoding("float") == (True, 1)
    assert bath._encoding("int0") == (False, 0)
    assert bath._encoding("int2") == (False, 2)
    for bad in ("int4", "double", ""):
        try:
            bath._encoding(bad)
            assert False, f"{bad!r} should be rejected"
        except SystemExit:
            pass


def test_sim_encoding_matches_bath():
    # The pymodbus simulator's wire encoding must round-trip against what bath.py
    # (via minimalmodbus) expects: two's-complement scaled ints, and big-word-order
    # IEEE floats at 2*native + 0x8000.
    sys.path.insert(0, "tools")
    import bath_sim as sim
    for temp, dec in [(0.0, 1), (25.3, 1), (-40.0, 2), (-79.99, 2), (125.0, 1)]:
        assert abs(sim.dec_int(sim.enc_int(temp, dec), dec) - round(temp, dec)) < 1e-9
    for v in [0.0, -40.0, 125.0, -12.345]:
        assert abs(sim.dec_float(sim.enc_float(v)) - v) < 1e-3
    assert sim.enc_float(1.0) == [0x3F80, 0x0000]        # minimalmodbus BYTEORDER_BIG
    assert sim.float_addr(1) == 0x8002 and sim.float_addr(2) == 0x8004
    # negative scaled int is a proper 16-bit two's complement
    assert sim.enc_int(-1.0, 1) == 0xFFF6               # round(-10) = -10 -> 0xFFF6


def test_float_address_offset():
    # The core protocol fix: floats live at 2*native + 0x8000.
    configure()
    b = bath.Bath("SIM", use_float=True)
    assert b._float_addr(1) == 0x8002      # PV
    assert b._float_addr(2) == 0x8004      # target setpoint (handbook example)
    assert b._float_addr(24) == 0x8030     # SP1


def test_int_roundtrip_matched_decimals():
    configure(ctrl_decimals=1, pv=20.0)
    b = bath.Bath("SIM", use_float=False, decimals=1)   # decimals match controller
    ok = b.set_setpoint(-40.0)
    assert ok, "setpoint should verify when decimals match"
    assert abs(b.read_setpoint() - (-40.0)) < 1e-6
    assert -80 < b.read_pv() < 25          # sane, signed handling for negatives


def test_int_decimals_mismatch_shows_in_pv():
    # Controller uses 1 decimal, but we tell Bath 2. The setpoint read-back does
    # NOT catch this (write and read scale by the same factor, so it cancels) --
    # but the PV read-out is off by exactly 10x, which is what --dry-run reveals.
    configure(ctrl_decimals=1, pv=20.0, sp1=20.0)          # bath sitting at 20 C
    b = bath.Bath("SIM", use_float=False, decimals=2)      # WRONG decimals
    assert abs(b.read_pv() - 2.0) < 0.05, "decimals mismatch must show in PV read"


def test_float_roundtrip():
    configure(ctrl_decimals=1, pv=5.0)
    b = bath.Bath("SIM", use_float=True)
    assert b.set_setpoint(-12.345)
    assert abs(b.read_setpoint() - (-12.345)) < 1e-4   # full resolution preserved


def test_set_setpoint_writes_sp1_and_reads_working_sp():
    configure(ctrl_decimals=2, pv=10.0)
    b = bath.Bath("SIM", use_float=False, decimals=2)
    b.set_setpoint(30.0)
    assert abs(b.read_setpoint() - 30.0) < 1e-6        # SP1 (reg 24)
    b.read_pv()                                        # triggers a sim step
    assert abs(b.read_working_setpoint() - 30.0) < 1e-6  # working SP followed SP1


def test_set_setpoint_rejected_is_detected():
    # Simulate a running program: writes ignored -> verify fails.
    configure(ctrl_decimals=1, pv=20.0, sp1=20.0, reject_writes=True)
    b = bath.Bath("SIM", use_float=False, decimals=1)
    assert b.set_setpoint(50.0) is False


def test_ramp_rate_written():
    configure(ctrl_decimals=1)
    b = bath.Bath("SIM", use_float=False, decimals=1)
    b.set_ramp_rate(5.0)
    assert abs(b.instr.sp_rate - 5.0) < 1e-6
    b.set_ramp_rate(None)                              # None -> OFF (0)
    assert abs(b.instr.sp_rate - 0.0) < 1e-6


def test_wait_until_stable_converges():
    configure(ctrl_decimals=2, pv=20.0, conv=0.6)
    b = bath.Bath("SIM", use_float=False, decimals=2)
    b.set_setpoint(-30.0)
    ok = b.wait_until_stable(-30.0, tol=0.05, window_s=0.05,
                             poll_s=0.01, timeout_s=5, verbose=False)
    assert ok and abs(b.read_pv() - (-30.0)) < 0.05


def test_wait_until_stable_times_out_when_stuck():
    # Steady-state offset of 0.5 C -> never inside a 0.05 band.
    configure(ctrl_decimals=2, pv=20.0, conv=0.6, offset=0.5)
    b = bath.Bath("SIM", use_float=False, decimals=2)
    b.set_setpoint(-30.0)
    t0 = time.time()
    ok = b.wait_until_stable(-30.0, tol=0.05, window_s=0.05,
                             poll_s=0.02, timeout_s=1.0, verbose=False)
    assert ok is False and (time.time() - t0) < 4      # returns, run would go on


def test_ramp_progresses_via_working_setpoint():
    configure(ctrl_decimals=1, pv=0.0, sp1=0.0, conv=1.0)
    b = bath.Bath("SIM", use_float=False, decimals=1)
    b.set_ramp_rate(2.0)                               # 2 deg per sim step
    b.set_setpoint(20.0)
    b.read_pv()                                        # one step
    wsp1 = b.read_working_setpoint()
    b.read_pv()                                        # another step
    wsp2 = b.read_working_setpoint()
    assert 0 < wsp1 < 20 and wsp1 < wsp2 <= 20         # working SP ramps up


def test_scaled_timeout_formula():
    cfg = {"timeout_per_10k": 30.0, "timeout_floor": 15.0}
    assert ca.plateau_timeout_min(cfg, 0) == 15
    assert ca.plateau_timeout_min(cfg, 2) == 15        # floor
    assert ca.plateau_timeout_min(cfg, 10) == 30
    assert ca.plateau_timeout_min(cfg, -80) == 240     # sign-independent, 30*8


def test_config_parser_and_expansion(tmp="/tmp/_test_param.txt"):
    # Modbus path: encoding is honoured.
    open(tmp, "w").write(
        "plateaus: -40; 0; 40\n"
        "plateau_minutes: 20   # inline comment must be stripped\n"
        "ramp_c_per_min: 5\n"
        "bath_protocol: modbus\n"
        "bath_slave: 3\n"
        "bath_encoding: int2\n")
    b = ca.read_bath_config(tmp)
    assert b["plateaus"] == [-40.0, 0.0, 40.0]
    assert b["minutes"] == [20.0, 20.0, 20.0]          # single -> all
    assert b["ramps"] == [5.0, 5.0, 5.0]               # single -> all
    assert (b["use_float"], b["decimals"]) == (False, 2)
    assert b["protocol"] == "modbus" and b["address"] == 3 and b["slave"] == 3


def test_config_parser_bisynch_default(tmp="/tmp/_test_param_bi.txt"):
    # Default protocol is bisynch: encoding is ignored, address defaults to 1.
    open(tmp, "w").write(
        "plateaus: 0\n"
        "plateau_minutes: 5\n")
    b = ca.read_bath_config(tmp)
    assert b["protocol"] == "bisynch"
    assert b["address"] == 1
    # A bogus encoding must NOT raise under bisynch (it is Modbus-only).
    open(tmp, "w").write(
        "plateaus: 0\nplateau_minutes: 5\nbath_encoding: nonsense\n")
    b = ca.read_bath_config(tmp)
    assert b["protocol"] == "bisynch"


def test_config_parser_errors(tmp="/tmp/_test_param_err.txt"):
    def parse(text):
        open(tmp, "w").write(text)
        return ca.read_bath_config(tmp)
    for bad in ("plateaus: 0;1\nplateau_minutes: 5;5;5\n",       # count mismatch
                "plateau_minutes: 5\n",                            # no plateaus
                "plateaus: " + ";".join(["1"] * 21) + "\nplateau_minutes: 5\n"):
        try:
            parse(bad)
            assert False, "bad config should raise SystemExit"
        except SystemExit:
            pass


def test_sprt_anchor_points():
    assert abs(sprt.ratio_to_temp_c(0.254210687, "Channel2") - 0.01) < 1e-3
    assert abs(sprt.ratio_to_temp_c(0.214602392, "Channel2") - (-38.8344)) < 1e-3
    assert abs(sprt.ratio_to_temp_c(0.256828407, "Channel3") - 0.01) < 1e-3
    # unknown channel falls back to glass SPRT
    assert sprt.ratio_to_temp_c(0.254210687) == sprt.ratio_to_temp_glas_c(0.254210687)


def test_ntc_counts_conversion():
    # Against real recorded counts and edge cases. Default is the mean lab-S4 curve.
    assert abs(ntc.counts_to_resistance(5812827) - 361210.0) < 1.0
    assert abs(ntc.counts_to_temp_c(5812827) - (-51.021)) < 0.01     # S4 mean
    assert abs(ntc.counts_to_temp_c(4540193) - (-42.968)) < 0.01
    # Beta diagnostic still available and reads ~4 C warmer.
    assert abs(ntc.counts_to_temp_c_beta(5812827) - (-46.661)) < 0.01
    assert ntc.counts_to_temp_c(ntc.ADC_FULLSCALE) is None       # divide by zero
    assert ntc.counts_to_temp_c(0) is None                       # R <= 0 -> None


def test_ntc_disconnected_detection_and_format():
    # Raw counts > 10_000_000 -> node not connected (temp None), warned in output.
    assert ntc.is_connected(5812827) is True
    assert ntc.is_connected(10_000_001) is False
    hdr = "N90_NTC1 || N91_NTC1 || N92_NTC1"
    data = "5812827 || 16777216 || 4540193"          # N91 open input
    pairs = ntc.ntc1_from_row(hdr, data)
    assert dict(pairs)["N91"] is None                 # flagged as disconnected
    assert abs(dict(pairs)["N90"] - (-51.021)) < 0.01
    out = ntc.format_ntc1(pairs)
    assert "!! Nodes not connected: N91" in out
    assert "N90=" in out and "N92=" in out
    # all disconnected -> only the warning
    allbad = ntc.format_ntc1([("N90", None), ("N91", None)])
    assert allbad == "!! Nodes not connected: N90 N91"


def test_ntc_multichannel_conversion_and_format():
    # NTC1/NTC2/TestSB per node, TestSB open on N96.
    hdr = ("N94_NTC1 | N94_NTC2 | N94_TestSB || "
           "N96_NTC1 | N96_NTC2 | N96_TestSB")
    data = ("5812827 | 5540969 | 4952001 || "
            "4540193 | 5812827 | 16777216")               # N96/TestSB open
    rows = ntc.ntc_from_row(hdr, data)                     # default NTC_CHANNELS
    d = dict(rows)
    # channel order preserved, all three present for N94
    assert [ch for ch, _ in d["N94"]] == ["NTC1", "NTC2", "TestSB"]
    assert abs(dict(d["N94"])["NTC1"] - (-51.021)) < 0.01
    assert dict(d["N96"])["TestSB"] is None               # open input -> None
    out = ntc.format_ntc(rows)
    assert "N94: NTC1=" in out and "NTC2=" in out and "TestSB=" in out
    assert "!! Not connected: N96/TestSB" in out
    # channel subset works and skips the rest
    only1 = ntc.ntc_from_row(hdr, data, channels=("NTC1",))
    assert [ch for ch, _ in dict(only1)["N94"]] == ["NTC1"]


def test_ntc1_from_row_as_used_by_legacy_worker():
    # calibration_log.py passes the header with its "SecondsElapsed; DateTimePC; "
    # prefix stripped, and the raw head line as the data block.
    header = "SecondsElapsed; DateTimePC; N94_NTC1 | N94_NTC2 || N96_NTC1 | N96_NTC2"
    cols = header.split(";", 2)[2]
    res = dict(ntc.ntc1_from_row(cols, "5812827 | 1 || 4540193 | 2"))
    assert abs(res["N94"] - (-51.021)) < 0.01
    assert abs(res["N96"] - (-42.968)) < 0.01
    assert ntc.ntc1_from_row(cols, cols) == []                  # echoed header line
    assert ntc.ntc1_from_row(cols, "New Node Array: 94 96") == []  # meta line


def _chan(res):
    """[(node,[(ch,t)])] -> {node: {ch: t}} for concise assertions."""
    return {node: dict(chans) for node, chans in res}


def test_ntc1_parser_simple(tmp="/tmp/_test_ntc_a.txt"):
    open(tmp, "w").write(
        "Group1; SecondsElapsed; DateTimePC; N90_NTC1 | N90_NTC2 | N90_TestSB || N91_NTC1 | N91_NTC2 | N91_TestSB\n"
        "Group1; 0.05; 2026-07-02 16:00:00.0; New Node Array: 90 91\n"
        "Group1; 9.19; 2026-07-02 16:00:09.1; 5812827 | 5540969 | 4952001 || 4540193 | 300 | 400\n")
    d = _chan(ca.latest_ntc_temps(tmp))            # all NTC channels, mean-S4 curve
    assert abs(d["N90"]["NTC1"] - (-51.021)) < 0.01
    assert abs(d["N90"]["NTC2"] - (-49.349)) < 0.01   # second channel converted too
    assert abs(d["N90"]["TestSB"] - (-45.652)) < 0.01 # TestSB is an NTC as well
    assert abs(d["N91"]["NTC1"] - (-42.968)) < 0.01


def test_ntc1_parser_standalone_and_order(tmp="/tmp/_test_ntc_b.txt"):
    # Hardest case: a leading standalone (TempADC) column AND NTC1 as the 2nd
    # per-node channel -> only header-anchored parsing gets the right column.
    open(tmp, "w").write(
        "Group1; SecondsElapsed; DateTimePC; TempADC || N90_NTC2 | N90_NTC1 || N91_NTC2 | N91_NTC1\n"
        "Group1; 9.19; 2026-07-02 16:00:09.1; 999 || 111 | 5812827 || 222 | 4540193\n")
    d = _chan(ca.latest_ntc_temps(tmp, channels=("NTC1",)))
    assert abs(d["N90"]["NTC1"] - (-51.021)) < 0.01
    assert abs(d["N91"]["NTC1"] - (-42.968)) < 0.01


def test_ntc1_parser_multigroup_picks_latest(tmp="/tmp/_test_ntc_c.txt"):
    open(tmp, "w").write(
        "Group1; SecondsElapsed; DateTimePC; N90_NTC1 || N91_NTC1\n"
        "Group1; 1.0; 2026-07-02 16:00:01.0; 5812827 || 5812827\n"
        "Group2; SecondsElapsed; DateTimePC; N95_NTC1 || N96_NTC1\n"
        "Group2; 2.0; 2026-07-02 16:00:02.0; 4540193 || 4540193\n")
    res = ca.latest_ntc_temps(tmp, channels=("NTC1",))   # last row is Group2
    assert [n for n, _ in res] == ["N95", "N96"]
    assert all(abs(dict(chans)["NTC1"] - (-42.968)) < 0.01 for _, chans in res)


def test_ntc1_status_no_data():
    assert ca.ntc_status("/tmp/_ntc_missing_xyz.txt") == "NTC=--"


def test_sprt_tail_status(tmp="/tmp/_test_microk.txt"):
    open(tmp, "w").write(
        "0.1;2026-07-02 18:00:00.1;0.2350000;0.56mA;Channel2;1\n"
        "0.2;2026-07-02 18:00:05.4;0.2345678;0.56mA;Channel2;2\n")
    s = ca.sprt_status(tmp)
    assert s.startswith("SPRT=") and "Channel2" in s
    assert ca.sprt_status("/tmp/_does_not_exist_xyz.txt") == "SPRT=--.-- C"


def test_run_schedule_end_to_end():
    configure(ctrl_decimals=2, pv=25.0, conv=0.6)
    b = bath.Bath("SIM", use_float=False, decimals=2)
    bath.run_schedule(b, [-40.0, 10.0], minutes=0.002, ramp=None,
                      tol=0.05, window_min=0.001, timeout_min=0.5)
    assert abs(b.read_pv() - 10.0) < 0.1               # ended near last setpoint


# --------------------------------------------------------------------------
# Runner (also discoverable by pytest via the test_* names)
# --------------------------------------------------------------------------
def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL  {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
