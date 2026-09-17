#!/usr/bin/env python3
"""Hardware-free tests for klimakammer.py against a small SimServ simulator.

    python3 schneehoehensensor/tests/test_klimakammer.py      (takes about 15 s)
"""
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "skripte" / "klimakammer.py"
SEP = "\xb6"


class SimServ(socketserver.BaseRequestHandler):
    """One command per connection, like the chamber. The actual temperature moves
    at most 5 K towards the setpoint per temperature query."""

    def handle(self):
        buf = b""
        while not buf.endswith(b"\r"):
            c = self.request.recv(1)
            if not c:
                return
            buf += c
        fields = buf.decode("latin-1").strip().split(SEP)
        cmd, args, s = fields[0], fields[2:], self.server.state
        with s["lock"]:
            answer, code = None, "1"
            if cmd == "11004":
                if args[0] == "1":
                    s["ist"] += max(-5.0, min(5.0, s["soll"] - s["ist"]))
                    answer = f"{s['ist']:.1f}"
                else:
                    answer = "40.0"
            elif cmd == "11002":
                answer = f"{s['soll']:.1f}" if args[0] == "1" else "50.0"
            elif cmd == "11001":
                s["soll"] = float(args[1])
            elif cmd == "10012":
                answer = str(s["status"])
            elif cmd == "14001":
                s["status"] |= 2
            elif cmd == "99997":
                code = "-8"                  # the lab chamber answers info with a read failure
        self.request.sendall((code + (SEP + answer if answer is not None else "") + "\r\n").encode("latin-1"))


def start_sim(status=3, soll=20.0, ist=20.0):
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), SimServ)
    srv.daemon_threads = True
    srv.state = {"soll": soll, "ist": ist, "status": status, "lock": threading.Lock()}
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def klima(port, *args, stdin="", laeufe=None):
    cmd = [sys.executable, str(SCRIPT), "--host", "127.0.0.1", "--port", str(port)]
    if laeufe:
        cmd += ["--laeufe", str(laeufe)]
    return subprocess.run(cmd + list(args), input=stdin, capture_output=True, text=True, timeout=60)


def test_status_reads_chamber():
    srv = start_sim()
    r = klima(srv.server_address[1], "status")
    srv.shutdown()
    assert r.returncode == 0, r.stderr
    assert "ist +20.0 °C, soll +20.0 °C" in r.stdout and "Status 3: läuft" in r.stdout, r.stdout
    assert "Kammer 127.0.0.1\n" in r.stdout, r.stdout      # info answered -8, status goes on


def test_setpoint_written_after_confirmation():
    srv = start_sim()
    r = klima(srv.server_address[1], "soll", "-40", stdin="j\n")
    srv.shutdown()
    assert r.returncode == 0 and "Sollwert gesetzt: -40.0 °C" in r.stdout, r.stdout + r.stderr
    assert srv.state["soll"] == -40.0


def test_setpoint_not_written_without_confirmation():
    srv = start_sim()
    r = klima(srv.server_address[1], "soll", "-40", stdin="n\n")
    srv.shutdown()
    assert r.returncode != 0 and srv.state["soll"] == 20.0, r.stdout + r.stderr


def test_setpoint_out_of_range_refused():
    srv = start_sim()
    r = klima(srv.server_address[1], "soll", "-80", "--ja")
    srv.shutdown()
    assert r.returncode != 0 and "außerhalb" in r.stderr and srv.state["soll"] == 20.0, r.stderr


def test_manual_mode_started_on_request():
    srv = start_sim(status=1)
    r = klima(srv.server_address[1], "soll", "10", "--ja", "--start")
    srv.shutdown()
    assert r.returncode == 0 and "Handbetrieb gestartet" in r.stdout, r.stdout + r.stderr
    assert srv.state["status"] & 2


def test_stufe_waits_holds_notifies_and_logs():
    srv = start_sim()
    laeufe = Path(tempfile.mkdtemp(prefix="klima_"))
    lauf = laeufe / "kammer_MB7574_20260917-100000"
    lauf.mkdir()
    r = klima(srv.server_address[1], "stufe", "-40", "--ja", "--halten", "0.05", "--vorwarnung", "0.03",
              "--intervall", "0.2", "--still", laeufe=laeufe)
    srv.shutdown()
    assert r.returncode == 0, r.stdout + r.stderr
    for text in ("Soll erreicht", ">>> Kammer: In", "Stufe messen", "Jetzt: ./kammer stufe -40"):
        assert text in r.stdout, (text, r.stdout)
    (log,) = lauf.glob("kammer_MB7574_20260917-100000_klima_T-40_*.txt")
    rows = [l for l in log.read_text().splitlines() if not l.startswith("#")]
    assert len(rows) >= 10 and rows[-1].split("\t")[1] == "-40.00", rows[-3:]


def test_ntfy_topic_default_from_env():
    import importlib.util, os
    spec = importlib.util.spec_from_file_location("klima", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    old = os.environ.get("KLIMA_NTFY")
    try:
        os.environ["KLIMA_NTFY"] = " test-thema "
        assert mod.default_ntfy() == "test-thema"
    finally:
        os.environ.pop("KLIMA_NTFY") if old is None else os.environ.__setitem__("KLIMA_NTFY", old)


def test_ntfy_sent_with_curl():
    # fake curl and osascript on PATH record their arguments instead of sending anything
    import importlib.util, os, types
    bin_dir = Path(tempfile.mkdtemp(prefix="klima_bin_"))
    for name in ("curl", "osascript"):
        (bin_dir / name).write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" >> "{bin_dir}/{name}.log"\n')
        (bin_dir / name).chmod(0o755)
    spec = importlib.util.spec_from_file_location("klima", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    old = os.environ["PATH"]
    try:
        os.environ["PATH"] = f"{bin_dir}:{old}"
        mod.notify("Stufe messen", "-70 °C seit 30 min", types.SimpleNamespace(still=False, ntfy="test-thema"))
    finally:
        os.environ["PATH"] = old
    sent = (bin_dir / "curl.log").read_text().splitlines()
    assert "Title: Stufe messen" in sent and "-70 °C seit 30 min" in sent and "https://ntfy.sh/test-thema" in sent, sent


def test_no_connection_is_reported():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]          # free port, nothing listening once closed
    r = klima(port, "status")
    assert r.returncode != 0 and "keine Verbindung zur Kammer" in r.stderr, r.stderr


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
