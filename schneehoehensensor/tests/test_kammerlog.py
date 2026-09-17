#!/usr/bin/env python3
"""Hardware-free tests for mb7574_kammerlog.py: a pseudo-terminal plays the sensor.

    python3 schneehoehensensor/tests/test_kammerlog.py      (takes about 25 s)
"""
import csv
import importlib.util
import os
import pty
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "skripte" / "mb7574_kammerlog.py"
# Real power-up header of the lab sensor (14.09.2026), with a stray byte in front.
HEADER = b"\xffSCXL-MaxSonar-WRS\rPN:MB7574\rCopyright 2011-2017\rMaxBotix Inc.\rRoHSv24b 084  0517\rTempI\r"

OLD_COLUMNS = ["start", "soll_C", "tag", "zyklen", "zyklen_mit_daten", "zyklen_mit_kopfzeile",
               "n_werte", "n_5000", "n_ungueltig", "median_mm", "min_mm", "max_mm",
               "abw_ref_pct", "strom_mA", "bemerkung", "datei"]


def load_logger():
    spec = importlib.util.spec_from_file_location("kammerlog", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(laeufe, *args, stdin=""):
    return subprocess.run([sys.executable, str(SCRIPT), "--laeufe", str(laeufe), *args],
                          input=stdin, capture_output=True, text=True, timeout=90)


def step(laeufe, soll, value, stdin):
    """One step with a simulated sensor: 3 cycles of 2 s on / 1 s off.
    Cycle 3 carries one garbled line, one R5000 and one R0500 among six readings."""
    master, slave = pty.openpty()
    name = os.ttyname(slave)
    os.close(slave)          # the logger refuses a port another process holds open
    t0 = time.time()

    def sensor():
        for k in range(3):
            time.sleep(max(0, t0 + 3.0 + 3 * k - time.time()))
            os.write(master, HEADER)
            for i in range(6):
                time.sleep(0.3)
                if k == 2 and i == 1:
                    os.write(master, b"\xf3\x81R0\r")
                elif k == 2 and i == 2:
                    os.write(master, b"R5000\r")
                elif k == 2 and i == 3:
                    os.write(master, b"R0500\r")
                else:
                    os.write(master, f"R{value:04d}\r".encode())

    threading.Thread(target=sensor, daemon=True).start()
    return run(laeufe, "stufe", str(soll), "--port", name, "--ein", "2", "--aus", "1",
               stdin=stdin)


class FakeSupply(threading.Thread):
    """EX355P on a pseudo-terminal, answering like the lab supply did on 17.09.2026."""

    def __init__(self, ident="Thurlby Thandar,EX355P,0,v2.00", amps="0.09", vout="05.00"):
        super().__init__(daemon=True)
        self.master, slave = pty.openpty()
        self.port = os.ttyname(slave)
        os.close(slave)
        self.idn, self.amps, self.vout = ident, amps, vout
        self.on, self.volts, self.limit, self.log = False, 1.0, 1.0, []

    def run(self):
        buf = b""
        while True:
            try:
                chunk = os.read(self.master, 256)
            except OSError:
                time.sleep(0.05)
                continue
            buf += chunk
            *lines, buf = buf.split(b"\n")
            for raw in lines:
                cmd = raw.decode().strip()
                if not cmd:
                    continue
                self.log.append(cmd)
                answer = None
                if cmd == "*IDN?":
                    answer = self.idn
                elif cmd.startswith("V "):
                    self.volts = float(cmd[2:])
                elif cmd.startswith("I "):
                    self.limit = float(cmd[2:])
                elif cmd == "V?":
                    answer = f"V {self.volts:05.2f}"
                elif cmd == "I?":
                    answer = f"I {self.limit:.2f}"
                elif cmd in ("ON", "OFF"):
                    self.on = cmd == "ON"
                elif cmd == "OUT?":
                    answer = "OUT ON" if self.on else "OUT OFF"
                elif cmd == "VO?":
                    answer = f"V {self.vout}" if self.on else "V 00.10"
                elif cmd == "IO?":
                    answer = f"I {self.amps}" if self.on else "I 0.00"
                if answer is not None:
                    os.write(self.master, (answer + "\r\n").encode())


def sensor_on_supply(supply, value, stop):
    """Sensor pseudo-terminal that sends its header when the supply switches on and then readings."""
    master, slave = pty.openpty()
    name = os.ttyname(slave)
    os.close(slave)

    def run():
        was_on, last = False, 0.0
        while not stop.is_set():
            try:
                if supply.on and not was_on:
                    time.sleep(0.1)
                    os.write(master, HEADER)
                    last = time.time()
                if supply.on and time.time() - last >= 0.3:
                    os.write(master, f"R{value:04d}\r".encode())
                    last = time.time()
            except OSError:
                pass
            was_on = supply.on
            time.sleep(0.02)

    threading.Thread(target=run, daemon=True).start()
    return name


def test_supply_switched_and_current_read():
    laeufe = Path(tempfile.mkdtemp(prefix="kammerlog_"))
    run(laeufe, "neu", stdin="x\nx\nx\n")
    supply = FakeSupply()
    supply.start()
    stop = threading.Event()
    sensor = sensor_on_supply(supply, 812, stop)
    r = run(laeufe, "stufe", "20", "--port", sensor, "--netzteil", supply.port,
            "--ein", "3", "--aus", "1", "--keine-bemerkung")
    stop.set()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "V 5.00" in supply.log and "I 0.15" in supply.log and "*RST" not in supply.log, supply.log
    assert supply.log.count("ON") == 3 and not supply.on, supply.log
    (lauf,) = [p for p in laeufe.iterdir() if p.is_dir()]
    with open(lauf / f"{lauf.name}_zusammenfassung.csv", newline="") as f:
        (row,) = list(csv.DictReader(f))
    assert (row["strom_mA"], row["zyklen_mit_daten"], row["zyklen_mit_kopfzeile"]) == ("90", "3", "3"), row


def test_overvoltage_switches_off_and_stops():
    laeufe = Path(tempfile.mkdtemp(prefix="kammerlog_"))
    run(laeufe, "neu", stdin="x\nx\nx\n")
    supply = FakeSupply(vout="12.00")          # e.g. a fault: output far above the setting
    supply.start()
    stop = threading.Event()
    sensor = sensor_on_supply(supply, 812, stop)
    r = run(laeufe, "stufe", "20", "--port", sensor, "--netzteil", supply.port,
            "--ein", "3", "--aus", "1", "--keine-bemerkung")
    stop.set()
    assert r.returncode != 0 and "über 5.3 V" in r.stderr and not supply.on, r.stdout + r.stderr
    assert supply.log.count("ON") == 1, supply.log


def test_changed_knob_setting_is_reset_before_switching_on():
    laeufe = Path(tempfile.mkdtemp(prefix="kammerlog_"))
    run(laeufe, "neu", stdin="x\nx\nx\n")
    supply = FakeSupply()
    supply.volts, supply.limit = 12.0, 1.0      # someone turned the knobs in local mode
    supply.start()
    stop = threading.Event()
    sensor = sensor_on_supply(supply, 812, stop)
    r = run(laeufe, "stufe", "20", "--port", sensor, "--netzteil", supply.port,
            "--ein", "1", "--aus", "0.5", "--zyklen", "1", "--keine-bemerkung")
    stop.set()
    assert r.returncode == 0, r.stdout + r.stderr
    assert supply.log.index("V 5.00") < supply.log.index("ON") and (supply.volts, supply.limit) == (5.0, 0.15)


def test_supply_selftest_runs():
    supply = FakeSupply()
    supply.start()
    r = subprocess.run([sys.executable, str(SCRIPT.parent / "ex355p.py"), "--port", supply.port, "--test",
                        "--zyklen", "1", "--ein", "1", "--aus", "1"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "Ende: Ausgang AUS" in r.stdout and not supply.on, r.stdout + r.stderr
    assert "Vorher eingestellt: 1.00 V, 1.00 A" in r.stdout, r.stdout


def test_wrong_device_on_supply_port_refused():
    laeufe = Path(tempfile.mkdtemp(prefix="kammerlog_"))
    run(laeufe, "neu", stdin="x\nx\nx\n")
    supply = FakeSupply(ident="Other Instrument,XYZ,0,v1")
    supply.start()
    stop = threading.Event()
    sensor = sensor_on_supply(supply, 812, stop)
    r = run(laeufe, "stufe", "20", "--port", sensor, "--netzteil", supply.port, "--keine-bemerkung")
    stop.set()
    assert r.returncode != 0 and "unerwartetes Gerät" in r.stderr and "ON" not in supply.log, r.stderr


def test_step_without_run_folder_stops():
    r = run(Path(tempfile.mkdtemp(prefix="kammerlog_")), "stufe", "20", "--port", "/dev/null")
    assert r.returncode != 0 and "Erst: ./kammer neu" in r.stderr, r.stderr


def test_busy_port_is_refused():
    laeufe = Path(tempfile.mkdtemp(prefix="kammerlog_"))
    run(laeufe, "neu", stdin="x\nx\nx\n")
    master, slave = pty.openpty()                 # this test process keeps the port open
    r = run(laeufe, "stufe", "20", "--port", os.ttyname(slave))
    os.close(slave); os.close(master)
    assert r.returncode != 0 and "schon geöffnet" in r.stderr, r.stdout + r.stderr


def test_short_command_runs():
    r = subprocess.run([str(SCRIPT.parent.parent / "kammer"), "stufe", "--help"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "Solltemperatur" in r.stdout, r.stdout + r.stderr


def test_old_summary_is_upgraded_from_the_log():
    # A summary written before median_z1_mm/drift_mm existed gets both from its step log,
    # and deviations are recomputed once a +20 C reference step arrives.
    kl = load_logger()
    lauf = Path(tempfile.mkdtemp(prefix="kammerlog_")) / "kammer_MB7574_20260914-170058"
    lauf.mkdir()
    log = f"{lauf.name}_T-20_170147.txt"
    lines = ["# MB7574 Kammertest", "# zeit\tt_s\tzyklus\tphase\tzeile"]
    for k, vals in ((1, [982, 981, 979]), (2, [984, 984, 985]), (3, [986, 987, 985])):
        lines.append(f"x\t0\t{k}\tEIN\t# Ansage Netzteil EIN")
        lines.append(f"x\t0\t{k}\tEIN\t\x00SCXL-MaxSonar-WRS")
        lines += [f"x\t0\t{k}\tEIN\tR{v:04d}" for v in vals]
    (lauf / log).write_text("\n".join(lines) + "\n")
    summary = lauf / f"{lauf.name}_zusammenfassung.csv"
    with open(summary, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(OLD_COLUMNS)
        w.writerow(["2026-09-14T17:01:47", "-20", "", "3", "3", "3", "9", "0", "0", "984", "979", "987",
                    "", "92", "", log])

    rows = kl.read_summary(summary)
    assert (rows[0]["median_z1_mm"], rows[0]["drift_mm"]) == ("981", "5"), rows[0]

    new = {k: "" for k in kl.SUMMARY_COLUMNS} | {"soll_C": "20", "median_z1_mm": "990", "datei": "x.txt"}
    kl.update_summary(summary, new)
    with open(summary, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert reader.fieldnames == kl.SUMMARY_COLUMNS
    assert [r["abw_ref_pct"] for r in rows] == ["-0.91", "0.00"], rows

    # a repeated reference step tagged "bezug" takes over from the first untagged +20 C step
    kl.update_summary(summary, {k: "" for k in kl.SUMMARY_COLUMNS}
                      | {"soll_C": "20", "tag": "bezug", "median_z1_mm": "1000", "datei": "y.txt"})
    with open(summary, newline="") as f:
        rows = list(csv.DictReader(f))
    assert [r["abw_ref_pct"] for r in rows] == ["-1.90", "-1.00", "0.00"], rows


def test_new_step_summary_and_close():
    laeufe = Path(tempfile.mkdtemp(prefix="kammerlog_"))

    r = run(laeufe, "neu", stdin="Maria, Thom\nSN-123\nTest\n")
    assert r.returncode == 0, r.stdout + r.stderr
    (lauf,) = [p for p in laeufe.iterdir() if p.is_dir()]
    assert re.fullmatch(r"kammer_MB7574_\d{8}-\d{6}", lauf.name), lauf.name
    meta = (lauf / f"{lauf.name}_meta.txt").read_text()
    assert "Serial number    : SN-123" in meta and "Code commit      : " in meta

    r = step(laeufe, 20, 812, "68\n\n")
    assert r.returncode == 0, r.stdout + r.stderr
    r = step(laeufe, -40, 830, "90,5\nTest Komma\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Zyklus 1 +2.2 % gegen +20 °C" in r.stdout and "Strom +33 % gegen +20 °C" in r.stdout, r.stdout
    assert "Median Zyklus 1 (Kaltstart) 830 mm, Anstieg bis Zyklus 3: +0 mm" in r.stdout, r.stdout

    with open(lauf / f"{lauf.name}_zusammenfassung.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [x["soll_C"] for x in rows] == ["20", "-40"]
    assert [x["abw_ref_pct"] for x in rows] == ["0.00", "2.22"], rows
    last = rows[1]
    assert (last["zyklen_mit_daten"], last["zyklen_mit_kopfzeile"]) == ("3", "3"), last
    assert (last["n_werte"], last["n_5000"], last["n_500"], last["n_ungueltig"]) == ("15", "1", "1", "1"), last
    assert "1 × R0500 (Echo näher als 50 cm)" in r.stdout and last["min_mm"] == "830", r.stdout
    assert (last["median_z1_mm"], last["drift_mm"]) == ("830", "0"), last
    assert (last["strom_mA"], last["bemerkung"]) == ("90.5", "Test Komma"), last
    assert last["datei"].startswith(f"{lauf.name}_T-40_") and (lauf / last["datei"]).exists()

    # abschliessen: first call writes the template, second refuses an unfilled one
    r = run(laeufe, "abschliessen")
    assert (lauf / "notizen.md").exists() and "Vorlage angelegt" in r.stdout, r.stdout
    r = run(laeufe, "abschliessen", stdin="j\n")
    assert "noch nicht ausgefüllt" in r.stdout, r.stdout


def test_run_folder_pushed_after_new_and_step():
    # local bare repository as remote; a second clone pushes in between, so the logger
    # has to pull --rebase once; uncommitted code changes stay out of the commits
    tmp = Path(tempfile.mkdtemp(prefix="kammerlog_git_"))

    def git(cwd, *args):
        r = subprocess.run(["git", "-C", str(cwd), "-c", "user.name=Test", "-c", "user.email=t@t", *args],
                           capture_output=True, text=True)
        assert r.returncode == 0, (args, r.stderr)
        return r.stdout

    git(tmp, "init", "-q", "--bare", "remote.git")
    git(tmp, "clone", "-q", "remote.git", "labor")
    labor = tmp / "labor"
    (labor / "code.py").write_text("x = 1\n")
    git(labor, "add", "code.py")
    git(labor, "commit", "-q", "-m", "code")
    git(labor, "push", "-q", "origin", "HEAD")
    git(tmp, "clone", "-q", "remote.git", "anderer")
    for clone in ("labor", "anderer"):
        git(tmp / clone, "config", "user.name", "Test")
        git(tmp / clone, "config", "user.email", "t@t")
    (labor / "code.py").write_text("x = 2\n")          # uncommitted code change on the lab laptop
    laeufe = labor / "laeufe"

    r = run(laeufe, "neu", stdin="x\nx\nx\n")
    assert r.returncode == 0 and "eingecheckt und gepusht" in r.stdout, r.stdout + r.stderr

    (tmp / "anderer" / "notiz.md").write_text("n\n")
    git(tmp / "anderer", "add", "notiz.md")
    git(tmp / "anderer", "commit", "-q", "-m", "anderswo")
    git(tmp / "anderer", "pull", "-q", "--rebase")
    git(tmp / "anderer", "push", "-q")

    r = step(laeufe, 20, 812, "68\n\n")
    assert r.returncode == 0 and "eingecheckt und gepusht" in r.stdout, r.stdout + r.stderr
    log = git(tmp / "remote.git", "log", "--format=%s", "--name-only")
    assert "Stufe +20 C" in log and "angelegt" in log and "anderswo" in log, log
    assert "_zusammenfassung.csv" in log and "code.py" not in log.split("anderswo")[0], log
    assert (labor / "code.py").read_text() == "x = 2\n"

    r = step(laeufe, -40, 830, "90\n\n")      # with --kein-push nothing new reaches the remote
    before = git(tmp / "remote.git", "rev-parse", "HEAD")
    r = run(laeufe, "neu", "--kein-push", stdin="x\nx\nx\n")
    assert r.returncode == 0 and "gepusht" not in r.stdout
    assert git(tmp / "remote.git", "rev-parse", "HEAD") == before


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
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
