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
