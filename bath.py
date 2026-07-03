#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Isotech calibration bath control (Eurotherm controller, Modbus RTU over RS232).

Target device: Isotech Libra Model 785 (785L, -80..125 C dual-chamber overflow
stirred liquid bath; Klasmeier "Doppelkammer-Kalibrierbad 785", art. KK-511).
Its native comms is RS422; an Isotech converter cable (ISO-232-432) adapts it
to a standard RS232 port. Per the Libra 785 manual the controller "uses the
Modbus Protocol", i.e. a Eurotherm 2000-series controller -- the same path
Isotech's own "Cal NotePad" (LabVIEW + Modbus) uses. This module mirrors it.

The same code works for the other Isotech baths (Hydra, Europa, Venus, ...);
only slave address and register/value encoding may differ per unit.

This is the missing piece the README calls a "possible next step": instead of
setting the bath temperature by hand, drive the setpoint from the PC and read
back the process value (PV) to decide when the bath is stable.

    Read PV, set SP interactively:
        python3 bath.py

    Programmatic use:
        from bath import Bath
        bath = Bath("/dev/cu.usbserial-XXXX", slave=1)
        print(bath.read_pv())          # current bath temperature (deg C)
        bath.set_setpoint(0.01)        # go to 0.01 deg C
        bath.wait_until_stable(0.01)   # block until within tolerance & steady

------------------------------------------------------------------------------
HARDWARE-DEPENDENT -- VERIFY ONCE ON THE REAL CONTROLLER
------------------------------------------------------------------------------
Two things differ between controller models / configurations and MUST be
checked against your unit before trusting the numbers (see README notes):

  1. Slave address       Default = 1. Only differs if several controllers
                         share one RS232 bus.

  2. Value encoding      Series 2000 by default returns SCALED INTEGERS with an
                         implied decimal point, e.g. register value 2503 == 250.3
                         So a reading of "0.01 C" comes back as 1 with DECIMALS=2.
                         Newer controllers can also expose IEEE floats in a
                         separate register block (full-resolution comms). Set
                         USE_FLOAT=True in that case.

Serial defaults below (9600 8N1) are the common Eurotherm Modbus setting; the
exact baud/parity is configurable in the controller's comms menu -- match it.

Register map: standard Eurotherm Series 2000 Modbus addresses. Full table in
the "Series 2000 Communications Handbook" (Eurotherm HA026230).
"""

import sys
import time
import argparse

try:
    import minimalmodbus
except ImportError:
    sys.exit(
        "This module needs 'minimalmodbus' (pip install minimalmodbus).\n"
        "It sits on top of pyserial and implements Modbus RTU."
    )

import serial.tools.list_ports


# --------------------------------------------------------------------------
# Controller configuration -- adjust to your unit (see header)
# --------------------------------------------------------------------------
BAUDRATE = 9600
BYTESIZE = 8
PARITY   = "N"          # Modbus RTU: none. (EI-Bisynch would be even/7-bit.)
STOPBITS = 1
TIMEOUT  = 1.0          # seconds

USE_FLOAT = False       # False = scaled-integer registers (see header)
DECIMALS  = 1           # implied decimal places when USE_FLOAT is False
# RECOMMENDED: scaled integers (USE_FLOAT=False). They are the native, unambiguous
# Series 2000 representation and are plenty for bath control -- 1 decimal already
# covers -80..125 C at 0.1 C, 2 decimals at 0.01 C (the calibration precision comes
# from the SPRT, not the bath's own display). DECIMALS must MATCH the controller's
# configured decimal places -- the --dry-run read-out reveals a wrong choice (PV
# off by a factor of 10). Float mode is available but adds two gotchas (see below).

# Standard Series 2000 Modbus register addresses (native / scaled-integer region)
REG_PV               = 1    # Process value (measured temperature) -- read only
REG_TARGET_SETPOINT  = 2    # Target setpoint            -- read/write
REG_OUTPUT           = 3    # % output power             -- read only
REG_WORKING_SETPOINT = 5    # Working setpoint (rate-limited, applied) -- read only
REG_SETPOINT_1       = 24   # Setpoint 1 (SP1)           -- read/write
REG_SETPOINT_2       = 25   # Setpoint 2 (SP2)           -- read/write
REG_SP_RATE_LIMIT    = 35   # Setpoint ramp rate limit  -- read/write

# Which register set_setpoint() writes. SP1 (24) is the persistent, always-writable
# stored setpoint and matches the front-panel setpoint in the normal single-SP mode
# Isotech baths run in. Some setups instead expect the Target Setpoint (2); if the
# bath does not move, switch SP_WRITE_REGISTER to REG_TARGET_SETPOINT.
SP_WRITE_REGISTER = REG_SETPOINT_1

# Full-resolution IEEE floats live in a SEPARATE Modbus region: for a native
# register N the float occupies two registers at (2*N + 0x8000). We must apply
# this offset in float mode -- reading the native address as a float is garbage.
# (Series 2000 Comms Handbook HA026230, ch. 6.) Byte/word order is not verified
# here, so if float values look scrambled, prefer scaled integers.
IEEE_FLOAT_BASE = 0x8000


class Bath:
    """Thin wrapper around a Eurotherm 2000-series controller over Modbus RTU."""

    def __init__(self, port, slave=1, use_float=USE_FLOAT, decimals=DECIMALS,
                 sp_register=SP_WRITE_REGISTER):
        # Value encoding differs per controller/config (see header):
        #   use_float=True  -> IEEE754 floats in the 0x8000 region (full resolution)
        #   use_float=False -> scaled integers with `decimals` implied places
        self.use_float   = use_float
        self.decimals    = decimals
        self.sp_register = sp_register
        self.instr = minimalmodbus.Instrument(port, slave)
        s = self.instr.serial
        s.baudrate = BAUDRATE
        s.bytesize = BYTESIZE
        s.parity   = PARITY
        s.stopbits = STOPBITS
        s.timeout  = TIMEOUT
        self.instr.mode = minimalmodbus.MODE_RTU
        self.instr.clear_buffers_before_each_transaction = True

    # -- low level -------------------------------------------------------
    def _float_addr(self, reg):
        return IEEE_FLOAT_BASE + 2 * reg          # Series 2000 IEEE region

    def _read(self, reg):
        if self.use_float:
            return self.instr.read_float(self._float_addr(reg))   # 2 regs, IEEE754
        return self.instr.read_register(reg, self.decimals, signed=True)

    def _write(self, reg, value):
        if self.use_float:
            self.instr.write_float(self._float_addr(reg), float(value))
        else:
            self.instr.write_register(reg, float(value), self.decimals, signed=True)

    # -- high level ------------------------------------------------------
    def read_pv(self):
        """Current bath temperature (process value) in deg C."""
        return self._read(REG_PV)

    def read_setpoint(self):
        """The commanded target setpoint (the register set_setpoint writes)."""
        return self._read(self.sp_register)

    def read_working_setpoint(self):
        """The applied, rate-limited working setpoint (deg C). Read-only.

        Useful to see whether the setpoint write actually took effect and how a
        ramp is progressing -- it differs from the commanded target while ramping.
        """
        return self._read(REG_WORKING_SETPOINT)

    def read_output(self):
        """Heater/cooler output in %."""
        return self._read(REG_OUTPUT)

    def set_setpoint(self, temperature, verify=True, tol=0.2):
        """Command a new setpoint (deg C) and read it back to confirm it took.

        Writes SP_WRITE_REGISTER (SP1 by default). Returns True if the read-back
        matches within `tol`. A mismatch usually means the wrong setpoint register
        or a running program (Eurotherm rejects setpoint writes while a program
        runs -- put it into hold/reset first).
        """
        self._write(self.sp_register, temperature)
        if not verify:
            return True
        readback = self._read(self.sp_register)
        ok = abs(readback - temperature) <= tol
        if not ok:
            print(f"  !! setpoint read-back {readback:+.3f} C != commanded "
                  f"{temperature:+.3f} C -- check SP_WRITE_REGISTER / program state.")
        return ok

    def set_ramp_rate(self, c_per_min):
        """Limit the setpoint approach rate to `c_per_min` deg C/min.

        Pass 0 (or None) to switch the rate limit OFF -> the bath approaches the
        setpoint as fast as it can. Optional: only needed to avoid thermal shock
        on the reference thermometer or to curb overshoot.
        """
        self._write(REG_SP_RATE_LIMIT, 0 if c_per_min is None else c_per_min)

    def wait_until_stable(self, target, tol=0.005, window_s=120,
                          poll_s=5.0, timeout_s=3600, verbose=True, extra=None):
        """Block until PV is within +/-tol of target and stays there for window_s.

        Returns True on success, False if timeout_s elapses first. Tune tol to
        the bath's real stability (0.005 C is aggressive for a stirred bath).

        `extra` is an optional callable returning a string appended to every
        status line (e.g. the live SPRT temperature); errors in it are ignored.
        """
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


# --------------------------------------------------------------------------
# Interactive port picker -- same style as calibration_log.py
# --------------------------------------------------------------------------
def pick_port(role, hint=""):
    ports = list(serial.tools.list_ports.comports())
    print("\nDetected serial ports:")
    for idx, p in enumerate(ports):
        print(f"  [{idx}]  {p.device}   |   {p.description}   |   {p.hwid}")
    default_idx = next((i for i, p in enumerate(ports) if hint and hint in p.device), None)
    prompt = f"Index for {role}"
    if default_idx is not None:
        prompt += f"  [Enter = {default_idx}: {ports[default_idx].device}]"
    prompt += ": "
    while True:
        choice = input(prompt).strip()
        if choice == "" and default_idx is not None:
            return ports[default_idx].device
        if choice.isdigit() and int(choice) < len(ports):
            return ports[int(choice)].device
        print("  Invalid - please enter one of the index numbers above.")


def _encoding(name):
    """Map a --encoding string to (use_float, decimals)."""
    name = name.lower()
    if name == "float":
        return True, 1
    if name in ("int0", "int1", "int2", "int3"):
        return False, int(name[-1])
    raise SystemExit(f"--encoding must be float|int0|int1|int2|int3 (got {name!r})")


def monitor(bath, interval=5.0):
    """Continuously print PV / target SP / working SP / output. Pure read-out.

    Showing target vs working setpoint makes it obvious whether a setpoint write
    took effect and how a ramp is progressing.
    """
    print(f"\nMonitoring bath (every {interval:g} s). Stop with Ctrl-C.")
    print(f"{'time':>8}   {'PV [C]':>10}   {'target [C]':>11}   "
          f"{'work SP [C]':>12}   {'OUT [%]':>8}")
    t0 = time.time()
    try:
        while True:
            print(f"{time.time()-t0:8.0f}   {bath.read_pv():+10.4f}   "
                  f"{bath.read_setpoint():+11.4f}   {bath.read_working_setpoint():+12.4f}   "
                  f"{bath.read_output():8.1f}")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped.")


def run_schedule(bath, plateaus, minutes, ramp, tol, window_min, timeout_min):
    """Step the bath through `plateaus` (deg C): ramp -> setpoint -> wait stable
    -> dwell `minutes`. No data logging -- just drives the bath and prints status.
    """
    try:
        for i, sp in enumerate(plateaus, 1):
            print(f"\n===== Plateau {i}/{len(plateaus)}: setpoint {sp:+.3f} C "
                  f"(dwell {minutes:g} min, ramp {'max' if ramp is None else str(ramp)+' C/min'}) =====")
            bath.set_ramp_rate(ramp)
            bath.set_setpoint(sp)
            ok = bath.wait_until_stable(sp, tol=tol, window_s=window_min * 60,
                                        poll_s=5.0, timeout_s=timeout_min * 60)
            if ok:
                print(f"  -> stable at {sp:+.3f} C. Holding for {minutes:g} min.")
            else:
                print(f"  !! NOT stable within {timeout_min:g} min. Holding anyway.")
            end = time.time() + minutes * 60
            while time.time() < end:
                remaining = end - time.time()
                print(f"  [plateau {i} hold] {remaining/60:5.1f} min left   "
                      f"PV={bath.read_pv():+.4f} SP={sp:+.3f}")
                time.sleep(min(15.0, remaining))
        print("\nSchedule complete. Bath left at its last setpoint.")
    except KeyboardInterrupt:
        print("\nInterrupted. Bath left at its current setpoint.")


def main():
    ap = argparse.ArgumentParser(
        description="Isotech/Eurotherm bath control & read-out (Modbus RTU, no logging)")
    ap.add_argument("--port", help="serial port, e.g. /dev/cu.usbserial-XXXX")
    ap.add_argument("--slave", type=int, default=1, help="Modbus slave address (default 1)")
    ap.add_argument("--encoding", default="int1",
                    help="register value encoding: float|int0|int1|int2|int3 (default int1)")
    # Modes (pick one; default = print current state once)
    ap.add_argument("--monitor", action="store_true",
                    help="continuously read out PV/SP/OUT until Ctrl-C")
    ap.add_argument("--interval", type=float, default=5.0, help="monitor poll interval [s]")
    ap.add_argument("--set", type=float, metavar="T", help="set setpoint to T deg C and exit")
    ap.add_argument("--wait", type=float, metavar="T",
                    help="set setpoint T, wait until stable (then hold --minutes if given)")
    ap.add_argument("--plateaus", metavar="\"a;b;c\"",
                    help="run a schedule of setpoints in deg C, e.g. \"-40;-20;0;20\"")
    ap.add_argument("--minutes", type=float, default=0.0,
                    help="hold/dwell time in minutes after stability (schedule / --wait)")
    ap.add_argument("--ramp-rate", type=float, default=None, dest="ramp_rate",
                    help="approach ramp in deg C/min (default: max speed)")
    ap.add_argument("--tol", type=float, default=0.02, help="stability band [deg C] (default 0.02)")
    ap.add_argument("--window", type=float, default=5.0,
                    help="minutes PV must stay in band to count as stable (default 5)")
    ap.add_argument("--timeout", type=float, default=120.0,
                    help="max minutes to wait for stability (default 120)")
    args = ap.parse_args()

    use_float, decimals = _encoding(args.encoding)
    port = args.port or pick_port("bath controller", hint="usbserial")
    bath = Bath(port, slave=args.slave, use_float=use_float, decimals=decimals)

    # Always show current state first -- this is also the smoke test that
    # confirms wiring, baud, slave address and value encoding are correct.
    print(f"\nPV (measured):  {bath.read_pv():+.4f} C")
    print(f"Setpoint:       {bath.read_setpoint():+.4f} C")
    print(f"Output:         {bath.read_output():.1f} %")

    if args.monitor:
        monitor(bath, interval=args.interval)
    elif args.plateaus:
        plateaus = [float(x) for x in args.plateaus.split(";") if x.strip()]
        run_schedule(bath, plateaus, args.minutes, args.ramp_rate,
                     args.tol, args.window, args.timeout)
    elif args.set is not None:
        bath.set_ramp_rate(args.ramp_rate)
        bath.set_setpoint(args.set)
        print(f"\n-> Setpoint written: {args.set:+.4f} C"
              f"{'' if args.ramp_rate is None else f' (ramp {args.ramp_rate} C/min)'}")
    elif args.wait is not None:
        bath.set_ramp_rate(args.ramp_rate)
        bath.set_setpoint(args.wait)
        print(f"\n-> Setpoint written: {args.wait:+.4f} C -- waiting for stability...")
        ok = bath.wait_until_stable(args.wait, tol=args.tol,
                                    window_s=args.window * 60, timeout_s=args.timeout * 60)
        print("STABLE." if ok else "TIMEOUT before stable.")
        if ok and args.minutes > 0:
            end = time.time() + args.minutes * 60
            while time.time() < end:
                remaining = end - time.time()
                print(f"  [hold] {remaining/60:5.1f} min left   PV={bath.read_pv():+.4f}")
                time.sleep(min(15.0, remaining))


if __name__ == "__main__":
    main()
