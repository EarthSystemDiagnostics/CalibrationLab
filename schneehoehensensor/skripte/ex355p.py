#!/usr/bin/env python3
"""Aim-TTi (Thurlby Thandar) EX355P bench supply over RS-232.

Lab set-up (17.09.2026): Delock USB-RS232 adapter (FTDI FT2232H, channel 0),
/dev/cu.usbserial-FT3GCNKB0, 9600 baud 8N1 (set on the supply on 17.09.2026, before 1200).
Commands end with LF, answers with CR LF; at least 10 ms between commands.
Set: 'V 5.00', 'I 0.15', 'ON', 'OFF'. Read: 'V?', 'I?', 'OUT?', 'VO?' (0.1 V
resolution), 'IO?' (10 mA), 'M?', 'ERR?', '*IDN?'. '*RST' is never sent (1 V, 1 A).
Any received character puts the supply into remote state, which locks its knobs
and On/Off key until 'Go to Local' is pressed.

Voltage guard for the MB7574 (2.7-5.5 V): the output is only switched on after
V? and I? read back 5.00 V / 0.15 A, and it is switched off at once if VO? ever
reads above V_MAX.

    python3 ex355p.py            # identify and read settings, switches nothing
    python3 ex355p.py --test     # without sensor: set 5.00 V / 0.15 A, 3 x 5 s on / 3 s off
"""
import argparse
import re
import time

import serial

DEFAULT_PORT = "/dev/cu.usbserial-FT3GCNKB0"
DEFAULT_BAUD = 9600
SET_VOLTS, SET_AMPS = 5.00, 0.15
V_MAX = 5.3                     # above this reading the output goes off immediately
NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")


class SupplyError(Exception):
    pass


class EX355P:
    def __init__(self, port=DEFAULT_PORT, baud=DEFAULT_BAUD, timeout=1.5):
        try:
            self.ser = serial.Serial(port, baud, timeout=timeout)
        except serial.SerialException as e:
            raise SupplyError(f"Netzteil-Port {port} nicht zu öffnen ({e})") from e
        self.port = port
        self.target = None          # (volts, amps) confirmed by setup()
        # A lone LF ends whatever is left in the supply's input buffer.
        self.ser.write(b"\n")
        time.sleep(0.3)
        self.ser.reset_input_buffer()

    def close(self):
        self.ser.close()

    def command(self, cmd):
        self.ser.write((cmd + "\n").encode("ascii"))
        time.sleep(0.05)

    def query(self, cmd):
        self.ser.write((cmd + "\n").encode("ascii"))
        answer = self.ser.readline().decode("ascii", "replace").strip()
        if not answer:
            raise SupplyError(f"Netzteil antwortet nicht auf {cmd} ({self.port})")
        return answer

    def value(self, cmd):
        m = NUMBER.search(self.query(cmd))
        if not m:
            raise SupplyError(f"Netzteil: keine Zahl in der Antwort auf {cmd}")
        return float(m.group())

    def identify(self):
        return self.query("*IDN?")

    def output_on(self):
        return self.query("OUT?").upper().endswith("ON")

    def check_settings(self):
        """Settings on the supply must still be the confirmed ones (knobs, other users)."""
        if self.target is None:
            raise SupplyError("Netzteil: Spannung und Strom sind nicht bestätigt, Ausgang bleibt aus")
        volts, amps = self.target
        v, i = self.value("V?"), self.value("I?")
        if abs(v - volts) > 0.02 or abs(i - amps) > 0.02:
            raise SupplyError(f"Netzteil steht auf {v:.2f} V / {i:.2f} A statt {volts:.2f} V / {amps:.2f} A, Ausgang bleibt aus")

    def setup(self, volts=SET_VOLTS, amps=SET_AMPS):
        """Output off, then set and read back voltage and current limit."""
        if volts > V_MAX:
            raise SupplyError(f"Sollspannung {volts:.2f} V über {V_MAX} V abgelehnt")
        self.off()
        self.command(f"V {volts:.2f}")
        self.command(f"I {amps:.2f}")
        self.target = (volts, amps)
        try:
            self.check_settings()
        except SupplyError:
            self.target = None
            raise

    def on(self):
        self.check_settings()
        self.command("ON")
        if not self.output_on():
            self.off()
            raise SupplyError("Netzteil meldet nach ON den Ausgang nicht als eingeschaltet")

    def off(self):
        self.command("OFF")

    def measure(self):
        """(volts, amps) at the output terminals; switches off above V_MAX."""
        volts, amps = self.value("VO?"), self.value("IO?")
        if volts > V_MAX:
            self.off()
            raise SupplyError(f"Ausgangsspannung {volts:.1f} V über {V_MAX} V, Ausgang ausgeschaltet")
        return volts, amps


def selftest(psu, cycles, on_s, off_s):
    before = f"{psu.value('V?'):.2f} V, {psu.value('I?'):.2f} A, Ausgang {'EIN' if psu.output_on() else 'AUS'}"
    print(f"Vorher eingestellt: {before}")
    psu.setup()
    print(f"Gesetzt und zurückgelesen: {SET_VOLTS:.2f} V, {SET_AMPS:.2f} A, Ausgang AUS")
    for k in range(1, cycles + 1):
        for phase, seconds in (("EIN", on_s), ("AUS", off_s)):
            if phase == "EIN":
                psu.on()
            else:
                psu.off()
            print(f"Zyklus {k}: {phase}")
            t0 = time.time()
            while (t := time.time() - t0) < seconds:
                v, a = psu.measure()
                print(f"  {t:4.1f} s  {v:4.1f} V  {a * 1000:3.0f} mA  Ausgang {'EIN' if psu.output_on() else 'AUS'}")
                time.sleep(1)
    print(f"Ende: Ausgang {'EIN' if psu.output_on() else 'AUS'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=DEFAULT_PORT)
    ap.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    ap.add_argument("--test", action="store_true", help="ohne Sensor: setzen, 3 x ein/aus, Werte mitlesen")
    ap.add_argument("--zyklen", type=int, default=3, help=argparse.SUPPRESS)
    ap.add_argument("--ein", type=float, default=5, help=argparse.SUPPRESS)
    ap.add_argument("--aus", type=float, default=3, help=argparse.SUPPRESS)
    a = ap.parse_args()
    psu = EX355P(a.port, a.baud)
    try:
        print("Kennung:", psu.identify())
        if a.test:
            selftest(psu, a.zyklen, a.ein, a.aus)
        else:
            print(f"Eingestellt: {psu.value('V?'):.2f} V, {psu.value('I?'):.2f} A, "
                  f"Ausgang {'EIN' if psu.output_on() else 'AUS'}")
            v, amps = psu.value("VO?"), psu.value("IO?")
            print(f"Gemessen: {v:.1f} V, {amps * 1000:.0f} mA")
    except SupplyError as e:
        raise SystemExit(f"Netzteil: {e}")
    except KeyboardInterrupt:
        raise SystemExit("\nAbgebrochen" + (", Ausgang wird ausgeschaltet" if a.test else ""))
    finally:
        if a.test:
            try:
                psu.off()
            finally:
                psu.close()
        else:
            psu.close()


if __name__ == "__main__":
    main()
