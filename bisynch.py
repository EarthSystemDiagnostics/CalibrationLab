#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EI-Bisynch (Eurotherm) client + scanner -- to reach a controller that does NOT
answer Modbus (e.g. an Isotech bath's Eurotherm 3504 set to EI-Bisynch while the
over-temperature limiter answers Modbus on the same serial bus).

EI-Bisynch is Eurotherm's ASCII protocol (ANSI X3.28-2.5 A4). Key facts:
  * Byte framing is usually 7 data bits, EVEN parity, 1 stop bit (7E1) -- NOT the
    8N1 that Modbus RTU uses. This scanner tries a few framings.
  * Parameters are two-letter mnemonics: PV = process value, SL = setpoint,
    OP = output, etc. A channel digit may prefix the mnemonic (e.g. 1PV).
  * Address = a group digit (GID, tens) + a unit digit (UID, units); each is sent
    twice, so address 12 -> "1122", address 1 -> "0011".

Read frame  (master -> instrument):  EOT GID GID UID UID <mnemonic> ENQ
Read reply  (instrument -> master):  STX <mnemonic><data> ETX BCC
Write frame (master -> instrument):  EOT GID GID UID UID STX <mnemonic><data> ETX BCC
Write reply:                         ACK (accepted) or NAK (rejected)
BCC = XOR of every byte after STX up to and including ETX.

Usage (read-only unless you use --write):
    python3 bisynch.py --port /dev/cu.usbserial-XXXX --scan
    python3 bisynch.py --port ... --addr 1 --read PV
    python3 bisynch.py --port ... --addr 1 --write SL -15     # writes a setpoint!
"""

import sys
import time
import argparse

try:
    import serial
except ImportError:
    sys.exit("This needs pyserial (python3 -m pip install pyserial).")

EOT, ENQ, STX, ETX, ACK, NAK = 0x04, 0x05, 0x02, 0x03, 0x06, 0x15

# Byte framings to try, most likely first. (bytesize, parity)
FRAMINGS = [(7, "E"), (7, "O"), (8, "E"), (8, "N")]

_BYTESIZE = {7: serial.SEVENBITS, 8: serial.EIGHTBITS}
_PARITY = {"E": serial.PARITY_EVEN, "O": serial.PARITY_ODD, "N": serial.PARITY_NONE}


def bcc(payload):
    """XOR block check of the given bytes (mnemonic + data + ETX)."""
    b = 0
    for x in payload:
        b ^= x
    return b


def _addr_digits(address):
    """Address -> the doubled GID/UID digit string, e.g. 1 -> '0011'."""
    gid, uid = address // 10, address % 10
    return f"{gid}{gid}{uid}{uid}"


def build_read(address, mnemonic):
    return bytes([EOT]) + (_addr_digits(address) + mnemonic).encode("ascii") + bytes([ENQ])


def build_write(address, mnemonic, value):
    body = (mnemonic + str(value)).encode("ascii") + bytes([ETX])
    return (bytes([EOT]) + _addr_digits(address).encode("ascii")
            + bytes([STX]) + body + bytes([bcc(body)]))


def parse_reply(reply, mnemonic):
    """Extract the data string from a read reply, or None if not a valid frame."""
    if not reply:
        return None
    i = reply.find(STX)
    j = reply.find(ETX, i + 1) if i >= 0 else -1
    if i < 0 or j < 0:
        return None
    text = reply[i + 1:j].decode("ascii", "replace")
    if text.startswith(mnemonic):
        text = text[len(mnemonic):]
    return text.strip()


class EIBisynch:
    def __init__(self, port, baud=9600, bytesize=7, parity="E", timeout=0.4):
        self.ser = serial.Serial(port, baudrate=baud, bytesize=_BYTESIZE[bytesize],
                                 parity=_PARITY[parity], stopbits=serial.STOPBITS_ONE,
                                 timeout=timeout)

    def read_param(self, address, mnemonic):
        self.ser.reset_input_buffer()
        self.ser.write(build_read(address, mnemonic))
        return parse_reply(self.ser.read(80), mnemonic)

    def write_param(self, address, mnemonic, value):
        self.ser.reset_input_buffer()
        self.ser.write(build_write(address, mnemonic, value))
        r = self.ser.read(4)
        return bool(r) and (ACK in r)

    def close(self):
        self.ser.close()


# Common Eurotherm EI-Bisynch mnemonics worth reading to map out a 3504.
# We read them all (read-only) and match against the front panel: the one equal
# to the panel setpoint is what we write to change it.
IDENTIFY_MNEMONICS = [
    ("PV", "process value (measured temperature)"),
    ("SL", "setpoint (target) -- often the writable one"),
    ("SP", "setpoint / working setpoint"),
    ("S1", "setpoint 1"),
    ("S2", "setpoint 2"),
    ("WS", "working setpoint (rate-limited)"),
    ("OP", "% output power"),
    ("SM", "setpoint select / mode"),
    ("RR", "setpoint ramp rate"),
    ("HS", "setpoint high limit"),
    ("LS", "setpoint low limit"),
    ("TG", "target setpoint (alt.)"),
]


def identify(port, address=1, baud=9600, bytesize=7, parity="E"):
    """Read a curated set of mnemonics from one Bisynch address. Read-only.

    Compare the results with the 3504 front panel. The mnemonic whose value
    equals the panel SETPOINT is the one to write with --write to steer the bath;
    the one equal to the measured temperature confirms PV.
    """
    print(f"\nEI-Bisynch identify on {port}, addr {address}, framing "
          f"{bytesize}{parity}1, read-only.")
    print("Match each value against the 3504 panel (PV = measured, WSP = setpoint).\n")
    dev = EIBisynch(port, baud=baud, bytesize=bytesize, parity=parity)
    found = []
    for mnem, label in IDENTIFY_MNEMONICS:
        try:
            val = dev.read_param(address, mnem)
        except Exception:
            val = None
        shown = repr(val) if val else "-- (no reply / not supported)"
        print(f"  {mnem:>3} = {shown:<28}  {label}")
        if val:
            found.append((mnem, val))
    dev.close()
    print("\nThe mnemonic equal to the panel setpoint is the writable target.")
    print("Then, read-only test a write path first by re-reading it; only after")
    print("that use:  python3 bisynch.py --port ... --addr 1 --write SL <value>")
    return found


class BisynchBath:
    """High-level bath controller over EI-Bisynch, API-compatible with bath.Bath.

    This is what actually drives an Isotech bath whose Eurotherm 3504 speaks
    EI-Bisynch (7E1) -- confirmed on the Libra 785: PV/SL/SP/OP/RR all answer at
    address 1. calibration_auto.py can use this or bath.Bath interchangeably.

    Mnemonics used:  PV (measured), SL (writable setpoint), OP (% output),
    RR (setpoint ramp rate, deg C/min; 0 = off).
    """

    def __init__(self, port, address=1, baud=9600, bytesize=7, parity="E",
                 timeout=0.4, decimals=2, setpoint_mnemonic="SL"):
        self.dev = EIBisynch(port, baud=baud, bytesize=bytesize, parity=parity,
                             timeout=timeout)
        self.address = address
        self.decimals = decimals
        self.sp_mnem = setpoint_mnemonic

    # -- low level -------------------------------------------------------
    def _read_num(self, mnemonic, retries=3):
        """Read a mnemonic and parse it as a float; retry a few times because a
        single Bisynch frame can be lost on a shared/noisy bus."""
        last = None
        for _ in range(retries):
            val = self.dev.read_param(self.address, mnemonic)
            if val:
                try:
                    return float(val)
                except ValueError:
                    last = val
            time.sleep(0.05)
        raise IOError(f"no valid {mnemonic!r} reading from Bisynch addr "
                      f"{self.address} (last={last!r})")

    def _write_num(self, mnemonic, value, retries=3):
        text = f"{float(value):.{self.decimals}f}"
        for _ in range(retries):
            if self.dev.write_param(self.address, mnemonic, text):
                return True
            time.sleep(0.05)
        return False

    # -- high level (mirrors bath.Bath) ----------------------------------
    def read_pv(self):
        """Current bath temperature (process value) in deg C."""
        return self._read_num("PV")

    def read_setpoint(self):
        """The commanded target setpoint (the mnemonic set_setpoint writes)."""
        return self._read_num(self.sp_mnem)

    def read_working_setpoint(self):
        """Working setpoint. The 3504 answers 'SP'; fall back to the target SP."""
        try:
            return self._read_num("SP")
        except IOError:
            return self._read_num(self.sp_mnem)

    def read_output(self):
        """Heater/cooler output in %."""
        return self._read_num("OP")

    def set_setpoint(self, temperature, verify=True, tol=0.2):
        """Command a new setpoint (deg C) via SL and read it back to confirm.

        Returns True if the read-back matches within `tol`. A missing ACK or a
        mismatch usually means the controller is in local/held mode."""
        ack = self._write_num(self.sp_mnem, temperature)
        if not verify:
            return ack
        readback = self._read_num(self.sp_mnem)
        ok = ack and abs(readback - temperature) <= tol
        if not ok:
            print(f"  !! setpoint read-back {readback:+.3f} C != commanded "
                  f"{temperature:+.3f} C (ack={ack}) -- controller in local/hold?")
        return ok

    def set_ramp_rate(self, c_per_min):
        """Limit the setpoint approach rate to `c_per_min` deg C/min via RR.
        Pass 0/None to switch the rate limit OFF (approach as fast as possible)."""
        self._write_num("RR", 0 if c_per_min is None else c_per_min)

    def wait_until_stable(self, target, tol=0.005, window_s=120,
                          poll_s=5.0, timeout_s=3600, verbose=True, extra=None):
        """Block until PV is within +/-tol of target for window_s. Returns True,
        or False if timeout_s elapses first. Identical semantics to bath.Bath."""
        def suffix():
            if extra is None:
                return ""
            try:
                return "   " + extra()
            except Exception:
                return ""

        t0 = time.time()
        in_band_since = None
        while time.time() - t0 < timeout_s:
            pv = self.read_pv()
            in_band = abs(pv - target) <= tol
            now = time.time()
            if in_band:
                in_band_since = in_band_since or now
                held = now - in_band_since
                if verbose:
                    print(f"  PV={pv:+.4f}  in band, held {held:5.0f}/{window_s}s{suffix()}")
                if held >= window_s:
                    return True
            else:
                in_band_since = None
                if verbose:
                    print(f"  PV={pv:+.4f}  (target {target:+.4f}, off by {pv-target:+.4f}){suffix()}")
            time.sleep(poll_s)
        return False

    def close(self):
        self.dev.close()


def monitor(port, address=1, baud=9600, interval=5.0):
    """Continuously print PV / setpoint / output. Pure read-out, changes nothing.

    Handy for watching the bath drive to a new setpoint (e.g. a -35 C flow test):
    set the setpoint once with --write SL -35, then run --monitor to see it get
    there. Stop with Ctrl-C."""
    b = BisynchBath(port, address=address, baud=baud)
    print(f"\nMonitoring 3504 on {port} addr {address} (every {interval:g} s). "
          f"Ctrl-C to stop.")
    print(f"{'time':>8}   {'PV [C]':>10}   {'setpoint [C]':>13}   {'OUT [%]':>8}")
    t0 = time.time()
    try:
        while True:
            try:
                line = (f"{time.time()-t0:8.0f}   {b.read_pv():+10.3f}   "
                        f"{b.read_setpoint():+13.3f}   {b.read_output():8.1f}")
            except Exception as e:
                line = f"{time.time()-t0:8.0f}   (read error: {type(e).__name__})"
            print(line)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        b.close()


def scan(port, addr_max=31, baud=9600, mnemonic="PV"):
    """Try every framing x address; report any EI-Bisynch device that answers."""
    print(f"\nEI-Bisynch scan on {port} (baud {baud}, reading '{mnemonic}'), read-only.")
    print("Trying 7E1/7O1/8E1/8N1 framings across addresses 0..{}.\n".format(addr_max))
    found = []
    for bytesize, parity in FRAMINGS:
        print(f"  framing {bytesize}{parity}1 ...")
        try:
            dev = EIBisynch(port, baud=baud, bytesize=bytesize, parity=parity)
        except Exception as e:
            print(f"    could not open port: {e}")
            continue
        for addr in range(0, addr_max + 1):
            try:
                val = dev.read_param(addr, mnemonic)
            except Exception:
                val = None
            if val:
                print(f"    RESPONSE  framing={bytesize}{parity}1 addr={addr}  {mnemonic}={val!r}")
                found.append((bytesize, parity, addr, val))
        dev.close()
    if found:
        print(f"\n{len(found)} EI-Bisynch response(s) -> the 3504 is on the bus in Bisynch.")
        print("Next: read PV and SL at that address and confirm against the panel.")
    else:
        print("\nNo EI-Bisynch device answered. The 3504 may use yet another baud,")
        print("or is on separate wires in the connector, or its comms config differs.")
    return found


def main():
    ap = argparse.ArgumentParser(description="EI-Bisynch (Eurotherm) scanner/client")
    ap.add_argument("--port", required=True, help="serial port, e.g. /dev/cu.usbserial-XXXX")
    ap.add_argument("--baud", type=int, default=9600, help="baud rate (default 9600)")
    ap.add_argument("--scan", action="store_true", help="probe framings x addresses (read-only)")
    ap.add_argument("--identify", action="store_true",
                    help="read a set of common mnemonics from --addr to find the "
                         "setpoint (read-only)")
    ap.add_argument("--monitor", action="store_true",
                    help="continuously read PV/setpoint/output from --addr until Ctrl-C")
    ap.add_argument("--interval", type=float, default=5.0,
                    help="seconds between --monitor reads (default 5)")
    ap.add_argument("--addr-max", type=int, default=31, dest="addr_max",
                    help="highest address to try in --scan (default 31)")
    ap.add_argument("--addr", type=int, default=1, help="device address for --read/--write")
    ap.add_argument("--bytesize", type=int, default=7, choices=[7, 8])
    ap.add_argument("--parity", default="E", choices=["E", "O", "N"])
    ap.add_argument("--read", metavar="MNEMONIC", help="read a parameter, e.g. PV or SL")
    ap.add_argument("--write", nargs=2, metavar=("MNEMONIC", "VALUE"),
                    help="write a parameter, e.g. --write SL -15  (this changes the setpoint!)")
    args = ap.parse_args()

    if args.scan:
        scan(args.port, addr_max=args.addr_max, baud=args.baud)
        return

    if args.identify:
        identify(args.port, address=args.addr, baud=args.baud,
                 bytesize=args.bytesize, parity=args.parity)
        return

    if args.monitor:
        monitor(args.port, address=args.addr, baud=args.baud, interval=args.interval)
        return

    dev = EIBisynch(args.port, baud=args.baud, bytesize=args.bytesize, parity=args.parity)
    if args.read:
        print(f"{args.read} = {dev.read_param(args.addr, args.read)!r}")
    elif args.write:
        mnem, val = args.write
        ok = dev.write_param(args.addr, mnem, val)
        print(f"write {mnem}={val}: {'ACK' if ok else 'no ACK / rejected'}")
    else:
        print(f"PV = {dev.read_param(args.addr, 'PV')!r}")
    dev.close()


if __name__ == "__main__":
    main()
