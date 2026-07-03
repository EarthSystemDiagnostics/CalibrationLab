#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Offline test suite for the EI-Bisynch bath path -- no hardware.

Run:   python3 test_bisynch.py            (also works under pytest)

It stubs pyserial with a FakeSerial that behaves like the Isotech Libra 785's
Eurotherm 3504 as observed on the real bus (7E1, address 1):

    read  frame  EOT 00 11 <MN> ENQ           -> STX <MN><value> ETX BCC
    write frame  EOT 00 11 STX <MN><value> ETX BCC -> ACK

so the frame construction, BCC, reply parsing AND the high-level BisynchBath
(read_pv / set_setpoint with read-back / set_ramp_rate / wait_until_stable) are
all exercised end to end against a faithful protocol model.
"""

import sys
import types
import time

EOT, ENQ, STX, ETX, ACK, NAK = 0x04, 0x05, 0x02, 0x03, 0x06, 0x15


# --------------------------------------------------------------------------
# Stub pyserial BEFORE importing bisynch
# --------------------------------------------------------------------------
def _bcc(payload):
    b = 0
    for x in payload:
        b ^= x
    return b


class FakeSerial:
    """A single Eurotherm 3504 answering EI-Bisynch at address 1.

    Holds the parameters we drive: PV (measured), SL/SP/S1 (setpoint, all alias
    the same value as the real unit does), OP (output %), RR (ramp rate). A write
    to SL/RR updates state and is ACKed; PV nudges toward SL on each read so
    wait_until_stable() can converge.
    """

    def __init__(self, port, baudrate=9600, bytesize=7, parity="E",
                 stopbits=1, timeout=0.4):
        self.params = {"PV": -15.0, "SL": -15.0, "SP": -15.0, "S1": -15.0,
                       "OP": 9.5, "RR": 0.0}
        self.parity = parity
        self._out = b""

    def reset_input_buffer(self):
        self._out = b""

    def write(self, data):
        # read request?  ... <MN> ENQ
        if data and data[-1] == ENQ:
            body = data[1:-1]                      # strip EOT ... ENQ
            mnem = body[4:].decode("ascii")        # after 4 address digits
            self._serve_read(mnem)
        elif STX in data:                          # write request
            i = data.index(STX)
            j = data.index(ETX)
            payload = data[i + 1:j].decode("ascii")
            mnem, value = payload[:2], payload[2:]
            if mnem in ("SL", "SP", "S1"):
                for k in ("SL", "SP", "S1"):
                    self.params[k] = float(value)
                self._out = bytes([ACK])
            elif mnem == "RR":
                self.params["RR"] = float(value)
                self._out = bytes([ACK])
            else:
                self._out = bytes([NAK])
        return len(data)

    def _serve_read(self, mnem):
        if mnem not in self.params:
            self._out = b""                        # unsupported -> no reply
            return
        # PV drifts toward the setpoint so the stability loop can settle.
        if mnem == "PV":
            self.params["PV"] += (self.params["SL"] - self.params["PV"]) * 0.6
        text = f"{mnem}{self.params[mnem]:.2f}".encode("ascii")
        frame = text + bytes([ETX])
        self._out = bytes([STX]) + frame + bytes([_bcc(frame)])

    def read(self, n=1):
        out, self._out = self._out[:n], self._out[n:]
        return out

    def close(self):
        pass


_serial = types.ModuleType("serial")
_serial.Serial = FakeSerial
_serial.SEVENBITS, _serial.EIGHTBITS = 7, 8
_serial.PARITY_EVEN, _serial.PARITY_ODD, _serial.PARITY_NONE = "E", "O", "N"
_serial.STOPBITS_ONE = 1
sys.modules["serial"] = _serial

import bisynch   # noqa: E402


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------
_results = []


def check(name, cond):
    _results.append((name, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'}  {name}")


def test_frames():
    # Read frame: EOT + doubled GID/UID + mnemonic + ENQ
    check("build_read addr1 PV", bisynch.build_read(1, "PV") == b"\x040011PV\x05")
    check("build_read addr12 PV", bisynch.build_read(12, "PV") == b"\x041122PV\x05")
    # BCC of 'SL-15' + ETX
    body = b"SL-15" + bytes([ETX])
    check("bcc matches XOR", bisynch.bcc(body) == _bcc(body))
    # parse a well-formed reply
    frame = b"PV-14.95" + bytes([ETX])
    reply = bytes([STX]) + frame + bytes([_bcc(frame)])
    check("parse_reply strips mnemonic", bisynch.parse_reply(reply, "PV") == "-14.95")
    check("parse_reply junk -> None", bisynch.parse_reply(b"garbage", "PV") is None)


def test_read():
    b = bisynch.BisynchBath("/dev/fake", address=1)
    check("read_pv float", abs(b.read_pv() - (-15.0)) < 1.0)
    check("read_setpoint == -15", abs(b.read_setpoint() - (-15.0)) < 1e-6)
    check("read_output == 9.5", abs(b.read_output() - 9.5) < 1e-6)
    b.close()


def test_setpoint_writeback():
    b = bisynch.BisynchBath("/dev/fake", address=1)
    ok = b.set_setpoint(-10.0)                     # writes SL, reads it back
    check("set_setpoint verified ok", ok is True)
    check("setpoint now -10", abs(b.read_setpoint() - (-10.0)) < 1e-6)
    b.close()


def test_ramp_rate():
    b = bisynch.BisynchBath("/dev/fake", address=1)
    b.set_ramp_rate(2.5)
    check("ramp rate written", abs(b._read_num("RR") - 2.5) < 1e-6)
    b.set_ramp_rate(None)                          # None -> 0 (off)
    check("ramp rate off == 0", abs(b._read_num("RR") - 0.0) < 1e-6)
    b.close()


def test_wait_until_stable():
    b = bisynch.BisynchBath("/dev/fake", address=1)
    b.set_setpoint(0.0)                            # PV will converge toward 0
    ok = b.wait_until_stable(0.0, tol=0.05, window_s=0.0, poll_s=0.0,
                             timeout_s=5.0, verbose=False)
    check("wait_until_stable converges", ok is True)
    b.close()


if __name__ == "__main__":
    test_frames()
    test_read()
    test_setpoint_writeback()
    test_ramp_rate()
    test_wait_until_stable()
    npass = sum(1 for _, ok in _results if ok)
    print(f"\n{npass} passed, {len(_results) - npass} failed")
    sys.exit(0 if npass == len(_results) else 1)
