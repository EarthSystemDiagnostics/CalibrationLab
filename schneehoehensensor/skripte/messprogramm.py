#!/usr/bin/env python3
"""Unattended chamber programme: for every temperature set the chamber, wait until the
chamber air has stayed within the tolerance for --stabil minutes, then measure one step
with the logger (supply switched by the script) and go on to the next temperature.

    ./programm 20 -40 -50 -60 -70 20
    ./programm 20 -40 -70 -70:60min 20:ende --stabil 30 --toleranz 1
    ./programm 20 -40 --neu --personen "Thom" --seriennummer MB7574-01 --beschreibung "..."

Temperatures come first, options after them. A temperature may carry a tag after a colon
(file name, as ./kammer stufe --tag). The same temperature twice in a row measures again
after another --stabil minutes.

Before the chamber is touched, the script checks chamber, supply and sensor port and asks
once for the whole programme (--ja skips the question). The stability timer restarts
whenever the chamber leaves the tolerance band. The programme stops, with a notification, on
a chamber alarm, when a step does not reach stability within --max-warten hours, or when a
step fails (supply error, overvoltage). The chamber setpoint then stays as it is; the supply is
off. A step that is only 'AUFFÄLLIG' is reported and the programme goes on.

Ctrl-C or 'kill <PID>' stop it the same way. Events go to <stem>_programm_<HHMMSS>.txt, chamber
readings to <stem>_klima_T<T>_<HHMMSS>.txt in the run folder; each step is pushed by the logger.
"""
import argparse
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ex355p import EX355P, SupplyError, DEFAULT_PORT as PSU_PORT, DEFAULT_BAUD as PSU_BAUD   # noqa: E402
from klimakammer import (HOST, PORT, T_MIN, T_MAX, STATUS_RUNNING, STATUS_ALARM, Chamber,   # noqa: E402
                         SimServError, default_ntfy, hm, notify, rate_per_min, running_text)
from mb7574_kammerlog import RUNS_DIR, find_port, pick_run, port_users, stop_on_sigterm   # noqa: E402

LOGGER = Path(__file__).resolve().parent / "mb7574_kammerlog.py"
STEP = re.compile(r"^([-+]?\d+(?:\.\d+)?)(?::([A-Za-z0-9_-]+))?$")


class ProgrammeError(Exception):
    pass


def split_steps(argv):
    """Leading temperatures (with optional ':tag') and the options after them; argparse would
    take '-70:60min' for an option."""
    steps = []
    for i, x in enumerate(argv):
        m = STEP.match(x)
        if not m:
            return steps, argv[i:]
        steps.append((float(m.group(1)), m.group(2) or ""))
    return steps, []


class Journal:
    """Event lines on the terminal and in the programme log."""

    def __init__(self, path):
        self.path = path
        self.f = open(path, "a") if path else None

    def __call__(self, text):
        line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')}  {text}"
        print(line, flush=True)
        if self.f:
            self.f.write(line + "\n")
            self.f.flush()

    def close(self):
        if self.f:
            self.f.close()


def preflight(a, k):
    """Read-only checks of chamber, supply and sensor port. Returns the sensor port."""
    try:
        st = k.status()
        ist, soll = k.actual(), k.setpoint()
    except SimServError as e:
        raise ProgrammeError(str(e)) from e
    print(f"Kammer: ist {ist:+.1f} °C, soll {soll:+.1f} °C, {running_text(st)}")
    if st & STATUS_ALARM:
        raise ProgrammeError(f"Kammer meldet Alarm (Status {st})")
    if not st & STATUS_RUNNING and not a.start:
        raise ProgrammeError("Die Kammer läuft nicht. Am Panel starten oder mit --start.")
    port = a.port or find_port()
    for p in (port, a.netzteil):
        users = port_users(p)
        if users:
            raise ProgrammeError(f"Port {p} ist schon geöffnet von {', '.join(users)}")
    try:
        psu = EX355P(a.netzteil, a.netzteil_baud)
        try:
            ident = psu.identify()
            on = psu.output_on()
        finally:
            psu.close()
    except SupplyError as e:
        raise ProgrammeError(f"Netzteil: {e}") from e
    if "EX355P" not in ident:
        raise ProgrammeError(f"unerwartetes Gerät am Netzteil-Port {a.netzteil}: {ident}")
    print(f"Netzteil: {ident}, Ausgang {'EIN' if on else 'AUS'}")
    print(f"Sensor-Port: {port}")
    return port


def set_chamber(a, k, t, journal):
    k.set_setpoint(t)
    new = k.setpoint()
    if abs(new - t) > 0.05:
        raise ProgrammeError(f"Kammer meldet Sollwert {new:+.1f} °C statt {t:+g} °C")
    journal(f"Kammer-Sollwert {t:+g} °C gesetzt")
    if not k.status() & STATUS_RUNNING:
        k.start_manual()
        time.sleep(2)
        if not k.status() & STATUS_RUNNING:
            raise ProgrammeError("Kammer läuft nach dem Start des Handbetriebs nicht")
        journal("Handbetrieb gestartet")


def wait_stable(a, k, t, run, journal):
    """Until the chamber has stayed within ±toleranz of t for a.stabil minutes."""
    path = run / f"{run.name}_klima_T{t:+g}_{time.strftime('%H%M%S')}.txt"
    deadline = time.time() + a.max_warten * 3600
    readings, since, last_error, told_errors = [], None, None, False
    with open(path, "w") as log:
        log.write(f"# Klimakammer {a.host}, Soll {t:+g} °C, Toleranz {a.toleranz:g} K, stabil {a.stabil:g} min "
                  "(Messprogramm)\n# zeit\tist_C\tsoll_C\tstatus\tfehler\n")
        while True:
            now = time.time()
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))
            if now > deadline:
                raise ProgrammeError(f"{t:+g} °C nach {a.max_warten:g} h nicht stabil")
            try:
                ist, soll, st = k.actual(), k.setpoint(), k.status()
            except (SimServError, ValueError, IndexError) as e:
                log.write(f"{stamp}\t\t\t\t{e}\n")
                log.flush()
                last_error = last_error or now
                if now - last_error > 600 and not told_errors:
                    notify("Kammer nicht erreichbar", f"seit {hm(last_error)}: {e}", a)
                    told_errors = True
                time.sleep(a.intervall)
                continue
            last_error, told_errors = None, False
            log.write(f"{stamp}\t{ist:.2f}\t{soll:.2f}\t{st}\t\n")
            log.flush()
            if st & STATUS_ALARM:
                raise ProgrammeError(f"Kammer-Alarm (Status {st}) bei ist {ist:+.1f} °C")
            if abs(soll - t) > 0.05:
                raise ProgrammeError(f"Kammer-Sollwert wurde auf {soll:+.1f} °C geändert")
            readings = [(x, v) for x, v in readings if now - x <= 600] + [(now, ist)]
            inside = abs(ist - t) <= a.toleranz
            if inside and since is None:
                since = now
                journal(f"{t:+g} °C erreicht (ist {ist:+.1f}), Messung um {hm(now + a.stabil * 60)}, "
                        f"wenn stabil")
            elif not inside and since is not None:
                journal(f"Toleranz verlassen (ist {ist:+.1f} °C), Haltezeit beginnt neu")
                since = None
            line = f"{hm(now)}  ist {ist:+.1f} °C, soll {soll:+.1f} °C"
            if since is None:
                r = rate_per_min(readings)
                if r and (t - ist) * r > 0:
                    line += f", {r:+.2f} K/min, erreicht ca. {hm(now + (t - ist) / r * 60)}"
            else:
                rest = since + a.stabil * 60 - now
                line += f", stabil seit {(now - since) / 60:.0f} min, Messung in {max(rest, 0) / 60:.0f} min"
                if rest <= 0:
                    print(line, flush=True)
                    return
            print(line, flush=True)
            time.sleep(a.intervall)


def measure(a, port, run, t, tag, journal):
    """One logger step; returns the summary lines. Raises ProgrammeError if the step failed."""
    cmd = [sys.executable, "-u", str(LOGGER), "--laeufe", str(a.laeufe), "stufe", f"{t:g}", "--lauf", str(run),
           "--port", port, "--netzteil", a.netzteil, "--netzteil-baud", str(a.netzteil_baud),
           "--keine-bemerkung", "--zyklen", str(a.zyklen), "--ein", f"{a.ein:g}", "--aus", f"{a.aus:g}"]
    if tag:
        cmd += ["--tag", tag]
    if a.kein_push:
        cmd.append("--kein-push")
    journal(f"Messung {t:+g} °C" + (f" ({tag})" if tag else "") + " beginnt")
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = b""
    try:
        while chunk := proc.stdout.read1(256):
            sys.stdout.buffer.write(chunk)
            sys.stdout.flush()
            out += chunk
        code = proc.wait()
    except KeyboardInterrupt:
        # Ctrl-C reaches the logger directly, kill <PID> only this script: pass it on either way
        # (the logger ignores a second signal) and wait until it has switched the supply off.
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise
    text = out.decode("utf-8", "replace").replace("\r", "\n")
    summary = [l for l in text.splitlines() if l.startswith(("Median Zyklus 1", "AUFFÄLLIG", "keine Auffälligkeit",
                                                            "Strom laut Netzteil", "Nicht gepusht"))]
    for line in summary:
        journal("  " + line)
    if code:
        tail = [l for l in text.splitlines() if l.strip()][-1:] or ["?"]
        raise ProgrammeError(f"Messung {t:+g} °C fehlgeschlagen (Code {code}): {tail[0]}")
    return summary


def main():
    steps, argv = split_steps(sys.argv[1:])
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                 usage="%(prog)s TEMPERATUR[:tag] ... [Optionen]")
    ap.add_argument("--stabil", type=float, default=30, help="Minuten innerhalb der Toleranz vor jeder Messung (Standard 30)")
    ap.add_argument("--toleranz", type=float, default=1.0, help="K um den Sollwert (Standard 1)")
    ap.add_argument("--max-warten", type=float, default=6, help="Stunden je Stufe bis zum Abbruch (Standard 6)")
    ap.add_argument("--intervall", type=float, default=30, help="Sekunden zwischen Kammerabfragen (Standard 30)")
    ap.add_argument("--zyklen", type=int, default=3)
    ap.add_argument("--ein", type=float, default=20, help="Sekunden Netzteil ein je Zyklus (Standard 20)")
    ap.add_argument("--aus", type=float, default=10, help="Sekunden Netzteil aus je Zyklus (Standard 10)")
    ap.add_argument("--neu", action="store_true", help="neuen Laufordner anlegen, sonst der neueste")
    ap.add_argument("--personen")
    ap.add_argument("--seriennummer")
    ap.add_argument("--beschreibung")
    ap.add_argument("--lauf", help="Laufordner (Name oder Pfad), Standard: der neueste")
    ap.add_argument("--ja", action="store_true", help="ohne Rückfrage starten")
    ap.add_argument("--start", action="store_true", help="Handbetrieb starten, falls die Kammer nicht läuft")
    ap.add_argument("--kein-push", action="store_true")
    ap.add_argument("--ntfy", default=default_ntfy())
    ap.add_argument("--still", action="store_true", help="keine Mac-Mitteilung, kein ntfy")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port-kammer", dest="kammer_port", type=int, default=PORT)
    ap.add_argument("--port", help="Sensor-Port, Standard: das TTL-232R-Kabel")
    ap.add_argument("--netzteil", default=PSU_PORT, help=f"Netzteil-Port, Standard {PSU_PORT}")
    ap.add_argument("--netzteil-baud", type=int, default=PSU_BAUD, help=argparse.SUPPRESS)
    ap.add_argument("--laeufe", type=Path, default=RUNS_DIR, help=argparse.SUPPRESS)   # tests only
    a = ap.parse_args(argv)
    if not steps:
        ap.error("keine Temperaturen angegeben, z. B.: ./programm 20 -40 -70 20")
    bad = [t for t, _ in steps if not T_MIN <= t <= T_MAX]
    if bad:
        sys.exit(f"außerhalb {T_MIN:+g} … {T_MAX:+g} °C: {', '.join(f'{t:+g}' for t in bad)}")
    stop_on_sigterm()
    k = Chamber(a.host, a.kammer_port)

    try:
        port = preflight(a, k)
    except ProgrammeError as e:
        sys.exit(f"Nicht gestartet: {e}")
    plan = "  ".join(f"{t:+g}" + (f":{tag}" if tag else "") for t, tag in steps)
    print(f"\nProgramm: {plan} °C\nJe Stufe: {a.stabil:g} min stabil (±{a.toleranz:g} K), dann {a.zyklen} × "
          f"{a.ein:g} s ein / {a.aus:g} s aus, Netzteil 5,00 V automatisch"
          + ("" if a.kein_push else ", Push nach jeder Stufe"))
    print("Laufordner: " + ("neu" if a.neu else pick_run(a).name))
    if not a.ja and input("Programm starten? [j/N] ").strip().lower() != "j":
        sys.exit("Nicht gestartet.")

    if a.neu:
        cmd = [sys.executable, str(LOGGER), "--laeufe", str(a.laeufe), "neu",
               "--personen", a.personen or "", "--seriennummer", a.seriennummer or "",
               "--beschreibung", a.beschreibung or f"Messprogramm {plan} °C"]
        if a.kein_push:
            cmd.append("--kein-push")
        if subprocess.run(cmd, stdin=subprocess.DEVNULL).returncode:
            sys.exit("Laufordner konnte nicht angelegt werden")
        a.lauf = None
    run = pick_run(a)
    journal = Journal(run / f"{run.name}_programm_{time.strftime('%H%M%S')}.txt")
    journal(f"Programm {plan} °C, stabil {a.stabil:g} min ±{a.toleranz:g} K, "
            f"{a.zyklen} × {a.ein:g}/{a.aus:g} s, PID {os.getpid()}")
    if not a.still:
        print(f"Hinweise aufs Handy über ntfy-Thema {a.ntfy}" if a.ntfy else "Kein ntfy-Thema: nur Mac-Mitteilungen")
    try:
        for i, (t, tag) in enumerate(steps, 1):
            name = f"{t:+g} °C" + (f" ({tag})" if tag else "")
            journal(f"Stufe {i}/{len(steps)}: {name}")
            if abs(k.setpoint() - t) > 0.05:
                set_chamber(a, k, t, journal)
            wait_stable(a, k, t, run, journal)
            summary = measure(a, port, run, t, tag, journal)
            result = "; ".join(l for l in summary if not l.startswith(("Strom", "Nicht gepusht")))
            nxt = f" Weiter mit {steps[i][0]:+g} °C." if i < len(steps) else ""
            notify(f"Stufe {name} gemessen", f"{result}{nxt}", a)
        journal("Programm fertig")
        notify("Messprogramm fertig", f"{plan} °C, Kammer-Sollwert bleibt {steps[-1][0]:+g} °C", a)
    except (ProgrammeError, SimServError) as e:
        journal(f"ABBRUCH: {e}")
        notify("Messprogramm abgebrochen", f"{e}. Netzteil aus, Kammer-Sollwert unverändert.", a)
        sys.exit(1)
    except KeyboardInterrupt:
        journal("Abgebrochen (Ctrl-C/kill). Netzteil aus, Kammer-Sollwert unverändert.")
        notify("Messprogramm gestoppt", "von Hand abgebrochen, Kammer-Sollwert unverändert", a)
        sys.exit(130)
    finally:
        journal.close()


if __name__ == "__main__":
    main()
