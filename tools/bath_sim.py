#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simulated Eurotherm Series 2000 Modbus-RTU slave for testing bath.py over a real
serial port -- WITHOUT the physical bath.

Why: tests/test_bath.py checks the logic against a fake in-process instrument, but it
does NOT exercise the real minimalmodbus RTU framing / serial round-trip. This
simulator does: it is a genuine Modbus slave you talk to over a serial port, so
bath.py -> minimalmodbus -> pyserial -> (pty) -> pymodbus goes through the actual
wire protocol. It validates framing, addressing, the 0x8000 float region and the
scaled-integer encoding end to end.

It also simulates simple bath physics: PV converges toward the working setpoint,
which follows SP1 (register 24) at the configured ramp rate (register 35).

--------------------------------------------------------------------------------
SETUP (macOS/Linux) -- two linked virtual serial ports via socat:
--------------------------------------------------------------------------------
    # 1) create a linked pty pair (leave this running in its own terminal):
    socat -d -d pty,raw,echo=0,link=/tmp/ttyBATH-sim \
                pty,raw,echo=0,link=/tmp/ttyBATH-client

    # 2) start the simulator on one end:
    python3 tools/bath_sim.py --port /tmp/ttyBATH-sim --encoding int1

    # 3) talk to it with bath.py on the other end:
    python3 bath.py --port /tmp/ttyBATH-client --encoding int1 --monitor
    python3 bath.py --port /tmp/ttyBATH-client --encoding int1 --set 25
    python3 bath.py --port /tmp/ttyBATH-client --encoding int1 \
                    --plateaus "-40;0;40" --minutes 1 --tol 0.2 --window 0.2

Install:  pip install pymodbus        (tested against pymodbus 3.x)
Note: over a pty, baud/parity are not enforced, so this validates framing +
addressing + encoding + logic, not the physical line settings.

Quick offline check of the encoding helpers (no pymodbus needed):
    python3 tools/bath_sim.py --selftest
"""

import sys
import time
import struct
import argparse
import threading

# --- Register map (must match bath.py) --------------------------------------
REG = {"pv": 1, "target": 2, "output": 3, "wsp": 5, "sp1": 24, "sp2": 25, "rate": 35}
IEEE_FLOAT_BASE = 0x8000
STORE_SIZE = 0x8100                    # covers native + IEEE float region


# --- Encoding helpers (pure Python; unit-testable without pymodbus) ----------
def enc_int(temp, decimals):
    """Temperature -> unsigned 16-bit holding-register value (two's complement)."""
    raw = int(round(temp * 10 ** decimals))
    if not -32768 <= raw <= 32767:
        raw = max(-32768, min(32767, raw))
    return raw & 0xFFFF


def dec_int(reg, decimals):
    """Holding-register value -> temperature (interpret as signed 16-bit)."""
    if reg >= 0x8000:
        reg -= 0x10000
    return reg / 10 ** decimals


def enc_float(value):
    """IEEE754 single -> two 16-bit registers, big word order (minimalmodbus BIG)."""
    b = struct.pack(">f", float(value))
    return [(b[0] << 8) | b[1], (b[2] << 8) | b[3]]


def dec_float(regs):
    """Two 16-bit registers (big word order) -> IEEE754 single."""
    b = bytes([regs[0] >> 8, regs[0] & 0xFF, regs[1] >> 8, regs[1] & 0xFF])
    return struct.unpack(">f", b)[0]


def float_addr(native_reg):
    return IEEE_FLOAT_BASE + 2 * native_reg


# --- Bath physics model ------------------------------------------------------
class BathModel:
    """First-order bath: PV -> working setpoint, working setpoint -> SP1 at rate."""

    def __init__(self, pv=20.0, conv=0.3):
        self.pv = pv
        self.wsp = pv
        self.sp1 = pv
        self.sp2 = pv
        self.target = pv
        self.rate = 0.0            # deg C/min; 0 = off (jump)
        self.output = 0.0
        self.conv = conv

    def step(self, dt_s):
        selected = self.sp1
        if self.rate > 0:
            delta = self.rate * dt_s / 60.0
            if abs(selected - self.wsp) <= delta:
                self.wsp = selected
            else:
                self.wsp += delta if selected > self.wsp else -delta
        else:
            self.wsp = selected
        self.pv += self.conv * (self.wsp - self.pv)
        self.output = max(-100.0, min(100.0, (self.wsp - self.pv) * 10.0))


# --- Datastore bridge (encode/decode the model into holding registers) -------
def read_control(store, encoding, decimals):
    """Return (sp1, rate) as written by the client, from the configured region."""
    if encoding == "float":
        sp1  = dec_float(store.getValues(3, float_addr(REG["sp1"]), 2))
        rate = dec_float(store.getValues(3, float_addr(REG["rate"]), 2))
    else:
        sp1  = dec_int(store.getValues(3, REG["sp1"], 1)[0], decimals)
        rate = dec_int(store.getValues(3, REG["rate"], 1)[0], decimals)
    return sp1, rate


def write_all(store, model, decimals):
    """Mirror the model state into BOTH the integer and the IEEE-float regions,
    so reads work whatever encoding the client uses."""
    values = {"pv": model.pv, "target": model.target, "output": model.output,
              "wsp": model.wsp, "sp1": model.sp1, "sp2": model.sp2, "rate": model.rate}
    for name, native in REG.items():
        v = values[name]
        store.setValues(16, native, [enc_int(v, decimals)])
        store.setValues(16, float_addr(native), enc_float(v))


def physics_loop(store, model, encoding, decimals, interval, stop):
    write_all(store, model, decimals)                 # initial state
    while not stop.is_set():
        sp1, rate = read_control(store, encoding, decimals)
        model.sp1, model.rate, model.target = sp1, rate, sp1
        model.step(interval)
        write_all(store, model, decimals)
        time.sleep(interval)


# --- Server ------------------------------------------------------------------
def serve(args):
    try:
        from pymodbus.datastore import (ModbusSequentialDataBlock,
                                        ModbusSlaveContext, ModbusServerContext)
        from pymodbus.server import StartSerialServer
    except ImportError:
        sys.exit("This simulator needs pymodbus (pip install pymodbus).")
    # RTU framer moved around across pymodbus versions -- try both spellings.
    try:
        from pymodbus.framer import FramerType
        framer = FramerType.RTU
    except ImportError:                                # pymodbus < 3.7
        from pymodbus.transaction import ModbusRtuFramer as framer

    decimals = 0 if args.encoding == "float" else int(args.encoding[-1])
    block = ModbusSequentialDataBlock(0, [0] * STORE_SIZE)
    # zero_mode=True -> protocol address N maps to datastore index N (no off-by-one)
    store = ModbusSlaveContext(hr=block, zero_mode=True)
    context = ModbusServerContext(slaves={args.slave: store}, single=False)

    model = BathModel(pv=args.pv, conv=args.conv)
    stop = threading.Event()
    t = threading.Thread(target=physics_loop,
                         args=(store, model, args.encoding, decimals, args.interval, stop),
                         daemon=True)
    t.start()

    print(f"Eurotherm simulator on {args.port}  (slave {args.slave}, "
          f"encoding {args.encoding}, start PV {args.pv} C). Ctrl-C to stop.")
    try:
        StartSerialServer(
            context=context, framer=framer, port=args.port,
            baudrate=args.baud, bytesize=8, parity="N", stopbits=1, timeout=1,
        )
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        print("\nSimulator stopped.")


# --- Offline self-test of the encoding (no pymodbus / no serial) -------------
def selftest():
    ok = True
    for temp, dec in [(0.0, 1), (25.3, 1), (-40.0, 1), (-79.99, 2), (125.0, 2)]:
        back = dec_int(enc_int(temp, dec), dec)
        exp = round(temp, dec)
        if abs(back - exp) > 10 ** (-dec) / 2 + 1e-9:
            print(f"FAIL int round-trip {temp} dec{dec}: got {back}"); ok = False
    for v in [0.0, 25.3, -40.0, -79.991, 125.0]:
        back = dec_float(enc_float(v))
        if abs(back - v) > 1e-3:
            print(f"FAIL float round-trip {v}: got {back}"); ok = False
    # float word order must match a manual big-endian pack
    regs = enc_float(1.0)                              # 1.0f = 0x3F800000
    if regs != [0x3F80, 0x0000]:
        print(f"FAIL float word order: {regs}"); ok = False
    if float_addr(2) != 0x8004 or float_addr(1) != 0x8002:
        print("FAIL float_addr offset"); ok = False
    # physics: converges toward SP1
    m = BathModel(pv=20.0, conv=0.5); m.sp1 = -30.0
    for _ in range(40):
        m.step(1.0)
    if abs(m.pv - (-30.0)) > 0.1:
        print(f"FAIL physics convergence: pv={m.pv}"); ok = False
    print("selftest: PASS" if ok else "selftest: FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Simulated Eurotherm 2000 Modbus-RTU slave")
    ap.add_argument("--port", help="serial port to serve on (e.g. /tmp/ttyBATH-sim)")
    ap.add_argument("--slave", type=int, default=1, help="Modbus slave address (default 1)")
    ap.add_argument("--encoding", default="int1",
                    help="int0|int1|int2|int3|float -- must match the client")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--pv", type=float, default=20.0, help="initial bath temperature")
    ap.add_argument("--conv", type=float, default=0.3, help="PV convergence per tick (0..1)")
    ap.add_argument("--interval", type=float, default=1.0, help="physics tick [s]")
    ap.add_argument("--selftest", action="store_true",
                    help="run offline encoding/physics checks and exit")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(selftest())
    if not args.port:
        ap.error("--port is required (or use --selftest)")
    serve(args)


if __name__ == "__main__":
    main()
