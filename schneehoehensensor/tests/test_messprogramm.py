#!/usr/bin/env python3
"""Hardware-free tests for messprogramm.py: SimServ chamber, EX355P and sensor on pseudo-terminals.

    python3 schneehoehensensor/tests/test_messprogramm.py      (takes about 40 s)
"""
import csv
import importlib.util
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_kammerlog import FakeSupply, sensor_on_supply   # noqa: E402
from test_klimakammer import start_sim                     # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / "skripte" / "messprogramm.py"
FAST = ["--stabil", "0.02", "--intervall", "0.2", "--zyklen", "2", "--ein", "1.5", "--aus", "0.5",
        "--ja", "--still", "--kein-push"]


def hardware(vout="05.00", status=3, soll=20.0, ist=20.0):
    srv = start_sim(status=status, soll=soll, ist=ist)
    supply = FakeSupply(vout=vout)
    supply.start()
    stop = threading.Event()
    sensor = sensor_on_supply(supply, 812, stop)
    return srv, supply, sensor, stop


def args(srv, supply, sensor, laeufe, *steps):
    return [sys.executable, str(SCRIPT), *steps, "--host", "127.0.0.1", "--port-kammer", str(srv.server_address[1]),
            "--port", sensor, "--netzteil", supply.port, "--laeufe", str(laeufe)]


def test_programme_runs_all_steps():
    srv, supply, sensor, stop = hardware()
    laeufe = Path(tempfile.mkdtemp(prefix="programm_"))
    r = subprocess.run(args(srv, supply, sensor, laeufe, "20", "-10", "-10:wdh") + FAST
                       + ["--neu", "--personen", "Test", "--seriennummer", "MB7574-01"],
                       capture_output=True, text=True, timeout=180)
    stop.set(); srv.shutdown()
    assert r.returncode == 0, r.stdout + r.stderr
    (lauf,) = [p for p in laeufe.iterdir() if p.is_dir()]
    assert "Serial number    : MB7574-01" in (lauf / f"{lauf.name}_meta.txt").read_text()
    with open(lauf / f"{lauf.name}_zusammenfassung.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [(x["soll_C"], x["tag"], x["zyklen_mit_daten"]) for x in rows] == \
        [("20", "", "2"), ("-10", "", "2"), ("-10", "wdh", "2")], rows
    assert len(list(lauf.glob("*_klima_T*.txt"))) == 3
    (prog,) = lauf.glob("*_programm_*.txt")
    text = prog.read_text()
    assert "Programm fertig" in text and "Kammer-Sollwert -10 °C gesetzt" in text, text
    assert text.count("Kammer-Sollwert") == 1, text          # same temperature: setpoint not set again
    assert srv.state["soll"] == -10.0 and not supply.on and "*RST" not in supply.log

    srv2 = start_sim(soll=-10.0, ist=-10.0)
    s = subprocess.run([sys.executable, str(SCRIPT), "status", "--laeufe", str(laeufe), "--host", "127.0.0.1",
                        "--port-kammer", str(srv2.server_address[1])], capture_output=True, text=True, timeout=30)
    srv2.shutdown()
    assert s.returncode == 0 and "Kein Messprogramm läuft" in s.stdout and "Programm fertig" in s.stdout, s.stdout + s.stderr
    assert "Kammer jetzt: ist -10.0 °C, soll -10.0 °C" in s.stdout and "Letzte Kammerabfrage" in s.stdout, s.stdout


def test_failed_step_stops_programme():
    srv, supply, sensor, stop = hardware(vout="12.00")
    laeufe = Path(tempfile.mkdtemp(prefix="programm_"))
    subprocess.run([sys.executable, str(SCRIPT.parent / "mb7574_kammerlog.py"), "--laeufe", str(laeufe), "neu",
                    "--personen", "", "--seriennummer", "", "--beschreibung", "", "--kein-push"], check=True,
                   capture_output=True)
    r = subprocess.run(args(srv, supply, sensor, laeufe, "20", "-40") + FAST, capture_output=True, text=True, timeout=120)
    stop.set(); srv.shutdown()
    assert r.returncode == 1 and "ABBRUCH" in r.stdout and "über 5.3 V" in r.stdout, r.stdout + r.stderr
    assert srv.state["soll"] == 20.0 and not supply.on       # -40 never set


def test_nothing_touched_on_bad_input_or_alarm():
    srv, supply, sensor, stop = hardware(status=3 | 8)
    laeufe = Path(tempfile.mkdtemp(prefix="programm_"))
    r = subprocess.run(args(srv, supply, sensor, laeufe, "20", "-80") + FAST, capture_output=True, text=True, timeout=60)
    assert r.returncode != 0 and "außerhalb" in r.stderr, r.stderr
    r = subprocess.run(args(srv, supply, sensor, laeufe, "-40") + FAST, capture_output=True, text=True, timeout=60)
    stop.set(); srv.shutdown()
    assert r.returncode != 0 and "Alarm" in r.stderr, r.stdout + r.stderr
    assert srv.state["soll"] == 20.0 and "ON" not in supply.log


def test_kill_during_measurement_switches_off():
    srv, supply, sensor, stop = hardware()
    laeufe = Path(tempfile.mkdtemp(prefix="programm_"))
    fast = [x if x != "1.5" else "20" for x in FAST]          # long on-phase
    p = subprocess.Popen(args(srv, supply, sensor, laeufe, "20") + fast + ["--neu"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    t0 = time.time()
    while not supply.on and time.time() - t0 < 60:
        time.sleep(0.1)
    assert supply.on, "supply never switched on"
    time.sleep(1)
    p.send_signal(signal.SIGTERM)
    out, _ = p.communicate(timeout=60)
    stop.set(); srv.shutdown()
    assert p.returncode == 130 and not supply.on and "Abgebrochen" in out, out
    assert supply.log[-1] == "OFF" or "OFF" in supply.log[supply.log.index("ON"):], supply.log


def test_stability_timer_restarts_outside_tolerance():
    spec = importlib.util.spec_from_file_location("programm", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    temps = iter([-39.5, -39.8, -42.0] + [-40.0] * 100)      # overshoot after first reaching

    class Chamber:
        actual = staticmethod(lambda: next(temps))
        setpoint = staticmethod(lambda: -40.0)
        status = staticmethod(lambda: 3)

    events = []
    a = types.SimpleNamespace(host="sim", toleranz=1.0, stabil=0.01, max_warten=1, intervall=0.05, still=True, ntfy=None)
    run = Path(tempfile.mkdtemp(prefix="programm_")) / "kammer_MB7574_20260917-180000"
    run.mkdir()
    mod.wait_stable(a, Chamber, -40.0, run, events.append)
    assert [e.split(" ")[0] for e in events] == ["-40", "Toleranz", "-40"], events


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
