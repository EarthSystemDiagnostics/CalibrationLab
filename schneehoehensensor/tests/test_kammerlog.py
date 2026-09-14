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
    Cycle 3 carries one garbled line and one R5000 among five readings."""
    master, slave = pty.openpty()
    t0 = time.time()

    def sensor():
        for k in range(3):
            time.sleep(max(0, t0 + 3.0 + 3 * k - time.time()))
            os.write(master, HEADER)
            for i in range(5):
                time.sleep(0.35)
                if k == 2 and i == 1:
                    os.write(master, b"\xf3\x81R0\r")
                elif k == 2 and i == 2:
                    os.write(master, b"R5000\r")
                else:
                    os.write(master, f"R{value:04d}\r".encode())

    threading.Thread(target=sensor, daemon=True).start()
    return run(laeufe, "stufe", str(soll), "--port", os.ttyname(slave), "--ein", "2", "--aus", "1",
               stdin=stdin)


def test_step_without_run_folder_stops():
    r = run(Path(tempfile.mkdtemp(prefix="kammerlog_")), "stufe", "20", "--port", "/dev/null")
    assert r.returncode != 0 and "Erst: mb7574_kammerlog.py neu" in r.stderr, r.stderr


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
    assert (last["n_werte"], last["n_5000"], last["n_ungueltig"]) == ("13", "1", "1"), last
    assert (last["median_z1_mm"], last["drift_mm"]) == ("830", "0"), last
    assert (last["strom_mA"], last["bemerkung"]) == ("90.5", "Test Komma"), last
    assert last["datei"].startswith(f"{lauf.name}_T-40_") and (lauf / last["datei"]).exists()

    # abschliessen: first call writes the template, second refuses an unfilled one
    r = run(laeufe, "abschliessen")
    assert (lauf / "notizen.md").exists() and "Vorlage angelegt" in r.stdout, r.stdout
    r = run(laeufe, "abschliessen", stdin="j\n")
    assert "noch nicht ausgefüllt" in r.stdout, r.stdout


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
