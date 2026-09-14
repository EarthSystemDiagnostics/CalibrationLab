#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Serial-port identification playground -- STANDALONE experiment tool.

It does NOT touch the calibration code. Use it to try out the two strategies for
"which device hangs on which port" and to learn each device's reply signature.

Strategy 1 (passive, deterministic):
    On macOS an FTDI adapter's /dev/cu.usbserial-<SERIAL> name comes from the
    chip's burned-in serial number -> stable across replug/reboot. So a role can
    be mapped to a serial-number substring once and matched forever.
        python3 tools/port_detect.py --list            # show every port + USB id
        python3 tools/port_detect.py --map config/param_combined.txt   # hint -> port

Strategy 2 (active probe):
    Open each port and run a device-specific handshake; classify by what answers.
    Read-oriented and safe: reads the bath PV (no setpoint write), sends the NTC
    head's 'help', and passively listens for the MicroK stream.
        python3 tools/port_detect.py --probe                       # all ports
        python3 tools/port_detect.py --probe --port /dev/cu.usbserial-XXXX
        python3 tools/port_detect.py --probe --listen 5            # longer listen

Nothing is written to any controller's setpoint/config. Run it with the
calibration NOT running (one program per port at a time).
"""

import os
import sys
import time
import argparse

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    sys.exit("This needs pyserial (python3 -m pip install pyserial).")

# Reuse the validated EI-Bisynch framing from the repo (import, don't reimplement).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    import bisynch
except Exception:
    bisynch = None


# --------------------------------------------------------------------------
# Strategy 1 -- enumerate ports and show the stable USB identity
# --------------------------------------------------------------------------
def list_ports():
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("No serial ports found.")
        return
    print(f"\n{len(ports)} serial port(s):\n")
    for p in ports:
        vidpid = (f"{p.vid:04x}:{p.pid:04x}" if p.vid is not None else "----:----")
        print(f"  {p.device}")
        print(f"      desc   : {p.description}")
        print(f"      usb    : VID:PID={vidpid}  serial={p.serial_number}  "
              f"loc={p.location}")
        print(f"      vendor : {p.manufacturer}   product={p.product}")
        print(f"      hwid   : {p.hwid}")
    print("\nStrategy 1: the 'serial=' above is burned into the adapter -> the")
    print("/dev/cu.usbserial-<serial> name is stable. Map each role to its serial.")


def map_hints(param_path):
    """Show which detected port each role's config hint resolves to."""
    hints = {}
    try:
        with open(param_path) as f:
            for line in f:
                line = line.strip()
                if ":" in line and not line.startswith("#"):
                    k, v = line.split(":", 1)
                    k = k.strip().lower(); v = v.split("#", 1)[0].strip()
                    if k in ("microk_port", "ntc_port", "bath_port"):
                        hints[k] = v
    except OSError as e:
        sys.exit(f"cannot read {param_path}: {e}")

    ports = list(serial.tools.list_ports.comports())
    print(f"\nRole -> port via config hints in {param_path}:\n")
    for role, hint in hints.items():
        matches = [p.device for p in ports if hint and hint in p.device]
        if len(matches) == 1:
            status = f"OK  -> {matches[0]}"
        elif not matches:
            status = "NO MATCH (adapter unplugged / different serial?)"
        else:
            status = f"AMBIGUOUS -> {matches}"
        print(f"  {role:12s} hint '{hint}'   {status}")


# --------------------------------------------------------------------------
# Strategy 2 -- active per-device probes
# --------------------------------------------------------------------------
def _open(port, baud, bytesize, parity, timeout=0.6):
    return serial.Serial(port, baudrate=baud, bytesize=bytesize, parity=parity,
                         stopbits=serial.STOPBITS_ONE, timeout=timeout)


def probe_bath(port):
    """Eurotherm 3504 over EI-Bisynch (7E1, 9600): read PV. Returns (hit, detail)."""
    if bisynch is None:
        return False, "bisynch module not importable"
    try:
        dev = bisynch.EIBisynch(port, baud=9600, bytesize=7, parity="E", timeout=0.5)
        vals = [dev.read_param(1, "PV") for _ in range(3)]
        dev.close()
        good = [v for v in vals if v]
        if good:
            return True, f"Bisynch PV(addr1)={good[-1]!r}"
        return False, "no Bisynch reply"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def probe_ntc(port):
    """SchwaRTech/AWI head (8N1, 19200): 'help' should wake it -> text banner."""
    try:
        ser = _open(port, 19200, serial.EIGHTBITS, serial.PARITY_NONE, timeout=0.8)
        ser.reset_input_buffer()
        ser.write(b"help\r\n")
        time.sleep(1.5)
        data = ser.read(4000).decode("utf-8", "replace")
        ser.close()
        text = data.strip()
        alpha = sum(ch.isalpha() for ch in text)
        tokens = [t for t in ("help", "node", "NTC", "SchwaR", "AWI", "version", "cmd")
                  if t.lower() in text.lower()]
        if alpha > 20 or tokens:
            return True, f"text banner ({alpha} alpha chars; tokens {tokens}) :: {text[:80]!r}"
        return False, (f"{len(text)} chars, no banner :: {text[:80]!r}" if text else "silent")
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def probe_microk(port, listen_s=3.0):
    """MicroK bridge (8N1, 9600): UNKNOWN command set -> listen passively, then try
    a couple of harmless identity queries. Report whatever comes back so we can
    learn its signature."""
    seen = []
    try:
        ser = _open(port, 9600, serial.EIGHTBITS, serial.PARITY_NONE, timeout=0.5)
        # 1) passive: does it stream on its own?
        ser.reset_input_buffer()
        t0 = time.time()
        passive = b""
        while time.time() - t0 < listen_s:
            passive += ser.read(512)
        if passive.strip():
            seen.append(("passive", passive.decode("utf-8", "replace")))
        # 2) try a few common identity queries (harmless read-only requests)
        for q in (b"*IDN?\r\n", b"\r\n", b"?\r\n"):
            ser.reset_input_buffer()
            ser.write(q)
            time.sleep(0.8)
            r = ser.read(512).decode("utf-8", "replace")
            if r.strip():
                seen.append((f"after {q!r}", r))
        ser.close()
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"

    def looks_microk(txt):
        # the logged format: t;datetime;ratio(E-notation);current(mA);ChannelN;idx
        # -- use MicroK-specific markers only (the NTC head also uses ';').
        return ("Channel" in txt or "mA" in txt or "E-0" in txt.upper())
    hit = any(looks_microk(t) for _, t in seen)
    if seen:
        sample = " | ".join(f"[{tag}] {t.strip()[:70]!r}" for tag, t in seen[:3])
        return hit, sample
    return False, "silent to passive listen and identity queries"


PROBES = [("bath (Bisynch 7E1)", probe_bath),
          ("ntc  (help 19200)",  probe_ntc),
          ("microk (listen 9600)", probe_microk)]


def probe_ports(ports, listen_s):
    print("\nActive probe -- read-oriented, safe. Each port is tried as every device.\n")
    for port in ports:
        print(f"=== {port} ===")
        hits = []
        for name, fn in PROBES:
            hit, detail = (fn(port, listen_s) if fn is probe_microk else fn(port))
            mark = "MATCH" if hit else "  -  "
            print(f"  [{mark}] {name:22s} {detail}")
            if hit:
                hits.append(name.split()[0])
        verdict = hits[0] if len(hits) == 1 else (f"ambiguous {hits}" if hits else "unknown")
        print(f"  -> best guess: {verdict}\n")


def main():
    ap = argparse.ArgumentParser(description="Serial-port identification playground")
    ap.add_argument("--list", action="store_true", help="enumerate ports + USB identity")
    ap.add_argument("--map", metavar="PARAM", help="resolve config port hints -> ports")
    ap.add_argument("--probe", action="store_true", help="actively identify each port")
    ap.add_argument("--port", help="probe only this port (with --probe)")
    ap.add_argument("--listen", type=float, default=3.0,
                    help="seconds to passively listen for the MicroK stream (default 3)")
    args = ap.parse_args()

    if args.map:
        map_hints(args.map)
    if args.probe:
        ports = [args.port] if args.port else [p.device for p in
                                               serial.tools.list_ports.comports()]
        probe_ports(ports, args.listen)
    if args.list or not (args.map or args.probe):
        list_ports()


if __name__ == "__main__":
    main()
