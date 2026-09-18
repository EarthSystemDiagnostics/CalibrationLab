#!/usr/bin/env python3
"""Unattended chamber programme: for every step set the chamber, wait until the chamber air
has stayed within the tolerance for the step's holding time, then measure the step with the
logger (supply switched by the script) and go on to the next step.

    ./programm 20 -40 -50 -60 -70 20
    ./programm 20 -40 -70 -70:60min 20:ende --stabil 30 --toleranz 1
    ./programm --plan programme/nacht_20h.txt --neu --personen "Thom" --seriennummer MB7574-01
    ./programm status        running or not, latest events, chamber now (changes nothing)

Temperatures come first, options after them. A temperature may carry a tag after a colon
(file name, as ./kammer stufe --tag). The same temperature twice in a row measures again
after another holding time.

Plan file (--plan): one step per line, '#' starts a comment, columns separated by blanks:

    # soll[:tag]   stabil_min  zyklen  ein_s  aus_s   feuchte_%rF
    -50:abkling    0           8       20     10,20,40,80,160,320,640
    -70:dauer      30          120     20     160
    20:feucht      30          0      -       -       80
    20:auftau      60          0

Missing columns or '-' take the command-line values (--stabil, --zyklen, --ein, --aus).
The last column sets the humidity setpoint before the step; the chamber controls humidity only
in the warm range, so a value above 0 %rF is refused below +5 C air temperature.
stabil 0 measures as soon as the chamber is within the tolerance; zyklen 0 only holds.
A list of off-times applies cycle by cycle.

Before the chamber is touched, the script checks chamber, supply and sensor port and asks
once for the whole programme (--ja skips the question). The holding timer restarts whenever
the chamber leaves the tolerance band. Chamber network errors are retried for --netz-geduld
minutes. The programme stops, with a notification, on a chamber alarm, when a step does not
reach stability within --max-warten hours, or when a step fails (supply error, overvoltage).
The chamber setpoint then stays as it is; the supply is off. A step that is only 'AUFFÄLLIG'
is reported and the programme goes on.

Ctrl-C or 'kill <PID>' stop it the same way. Events go to <stem>_programm_<HHMMSS>.txt, chamber
readings (also during the measurement) to <stem>_klima_T<T>_<HHMMSS>.txt in the run folder;
each step is pushed by the logger.
"""
import argparse
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ex355p import EX355P, SupplyError, DEFAULT_PORT as PSU_PORT, DEFAULT_BAUD as PSU_BAUD   # noqa: E402
from klimakammer import (HOST, PORT, T_MIN, T_MAX, RF_MIN, RF_MAX, RF_T_MIN, STATUS_RUNNING,   # noqa: E402
                         STATUS_ALARM, Chamber, SimServError, default_ntfy, hm, notify,
                         rate_per_min, running_text)
from mb7574_kammerlog import (RUNS_DIR, find_port, parse_off_times, pick_run, port_users,   # noqa: E402
                              stop_on_sigterm)

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


def read_plan(path, a):
    """Steps as dicts (soll, tag, stabil, zyklen, ein, aus) from a plan file."""
    steps = []
    for n, raw in enumerate(Path(path).read_text().splitlines(), 1):
        cols = raw.split("#", 1)[0].split()
        if not cols:
            continue
        m = STEP.match(cols[0])
        if not m or len(cols) > 6:
            raise ProgrammeError(f"{path}, Zeile {n}: erwartet 'soll[:tag] stabil zyklen ein aus feuchte': {raw.strip()}")
        cols += ["-"] * (6 - len(cols))
        try:
            step = {"soll": float(m.group(1)), "tag": m.group(2) or "",
                    "stabil": a.stabil if cols[1] == "-" else float(cols[1]),
                    "zyklen": a.zyklen if cols[2] == "-" else int(cols[2]),
                    "ein": a.ein if cols[3] == "-" else float(cols[3]),
                    "aus": a.aus if cols[4] == "-" else cols[4],
                    "feuchte": None if cols[5] == "-" else float(cols[5])}
            times = parse_off_times(step["aus"])
        except ValueError:
            raise ProgrammeError(f"{path}, Zeile {n}: keine Zahl: {raw.strip()}") from None
        if step["stabil"] < 0 or step["zyklen"] < 0 or step["ein"] <= 0 or not times or min(times) < 0:
            raise ProgrammeError(f"{path}, Zeile {n}: Werte außerhalb des Bereichs: {raw.strip()}")
        if step["feuchte"] is not None and not RF_MIN <= step["feuchte"] <= RF_MAX:
            raise ProgrammeError(f"{path}, Zeile {n}: Feuchte außerhalb {RF_MIN:g}…{RF_MAX:g} %rF: {raw.strip()}")
        steps.append(step)
    if not steps:
        raise ProgrammeError(f"{path}: keine Stufen")
    return steps


def step_name(s):
    return f"{s['soll']:+g} °C" + (f" ({s['tag']})" if s["tag"] else "")


def step_minutes(s):
    """Duration of the measurement itself in minutes (without waiting)."""
    offs = parse_off_times(s["aus"])
    off_total = sum(offs[k % len(offs)] for k in range(max(s["zyklen"] - 1, 0)))
    return (s["zyklen"] * s["ein"] + off_total + (5 if s["zyklen"] else 0)) / 60


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


def patiently(a, what, fn):
    """fn() with retries on chamber network errors for up to --netz-geduld minutes."""
    first = time.time()
    while True:
        try:
            return fn()
        except SimServError as e:
            if time.time() - first > a.netz_geduld * 60:
                raise SimServError(f"{what}: seit {hm(first)} keine Antwort ({e})") from e
            time.sleep(min(a.intervall, 10))


def set_chamber(a, k, t, journal):
    def write_and_check():
        k.set_setpoint(t)
        return k.setpoint()
    new = patiently(a, "Sollwert setzen", write_and_check)
    if abs(new - t) > 0.05:
        raise ProgrammeError(f"Kammer meldet Sollwert {new:+.1f} °C statt {t:+g} °C")
    journal(f"Kammer-Sollwert {t:+g} °C gesetzt")
    if not patiently(a, "Status lesen", k.status) & STATUS_RUNNING:
        patiently(a, "Handbetrieb starten", k.start_manual)
        time.sleep(2)
        if not patiently(a, "Status lesen", k.status) & STATUS_RUNNING:
            raise ProgrammeError("Kammer läuft nach dem Start des Handbetriebs nicht")
        journal("Handbetrieb gestartet")


def open_chamber_log(a, run, s):
    path = run / f"{run.name}_klima_T{s['soll']:+g}_{time.strftime('%H%M%S')}.txt"
    log = open(path, "w")
    log.write(f"# Klimakammer {a.host}, Soll {s['soll']:+g} °C, Toleranz {a.toleranz:g} K, stabil {s['stabil']:g} min"
              + (f", Feuchte-Soll {s['feuchte']:g} %rF" if s.get("feuchte") is not None else "")
              + " (Messprogramm; phase warten/messen)\n# zeit\tist_C\tsoll_C\tstatus\tfehler\tphase\trF\n")
    log.flush()
    return log


def poll(k, log, phase):
    """One chamber reading into the log: (ist, soll, status) or None after a network error."""
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        ist, soll, st = k.actual(), k.setpoint(), k.status()
    except (SimServError, ValueError, IndexError) as e:
        log.write(f"{stamp}\t\t\t\t{e}\t{phase}\t\n")
        log.flush()
        return None
    try:
        rf = f"{k.actual(2):.1f}"
    except (SimServError, ValueError, IndexError):
        rf = ""
    log.write(f"{stamp}\t{ist:.2f}\t{soll:.2f}\t{st}\t\t{phase}\t{rf}\n")
    log.flush()
    return ist, soll, st


def set_humidity(a, k, s, journal):
    """Humidity setpoint of a step. Above 0 %rF only in the warm range: below the dew point the
    chamber cannot control humidity and the evaporator ices up."""
    ziel = s["feuchte"]
    ist_t = patiently(a, "Temperatur lesen", k.actual)
    if ziel > 0 and ist_t < RF_T_MIN:
        raise ProgrammeError(f"Feuchte {ziel:g} %rF bei {ist_t:+.1f} °C abgelehnt (erst ab {RF_T_MIN:g} °C)")
    if abs(patiently(a, "Feuchte-Sollwert lesen", lambda: k.setpoint(2)) - ziel) <= 0.5:
        return
    patiently(a, "Feuchte setzen", lambda: k.set_setpoint(ziel, 2))
    neu = patiently(a, "Feuchte-Sollwert lesen", lambda: k.setpoint(2))
    if abs(neu - ziel) > 0.5:
        raise ProgrammeError(f"Kammer meldet Feuchte-Sollwert {neu:.1f} %rF statt {ziel:g} %rF")
    journal(f"Feuchte-Sollwert {ziel:g} %rF gesetzt")


class ChamberWatch(threading.Thread):
    """Chamber readings into the step's log while the logger measures."""

    def __init__(self, k, log, interval):
        super().__init__(daemon=True)
        self.k, self.log, self.interval = k, log, interval
        self.halt = threading.Event()

    def run(self):
        while not self.halt.is_set():
            poll(self.k, self.log, "messen")
            self.halt.wait(self.interval)


def wait_stable(a, k, s, log, journal):
    """Until the chamber has stayed within ±toleranz of the setpoint for s['stabil'] minutes."""
    t, hold = s["soll"], s["stabil"]
    deadline = time.time() + a.max_warten * 3600
    readings, since, last_error, told_errors, told_reached = [], None, None, False, False
    while True:
        now = time.time()
        if now > deadline:
            raise ProgrammeError(f"{t:+g} °C nach {a.max_warten:g} h nicht stabil")
        reading = poll(k, log, "warten")
        if reading is None:
            last_error = last_error or now
            if now - last_error > 600 and not told_errors:
                notify("Kammer nicht erreichbar", f"seit {hm(last_error)}, Messprogramm wartet weiter", a)
                told_errors = True
            time.sleep(a.intervall)
            continue
        if told_errors:
            journal(f"Kammer wieder erreichbar (nicht erreichbar seit {hm(last_error)})")
        last_error, told_errors = None, False
        ist, soll, st = reading
        if st & STATUS_ALARM:
            raise ProgrammeError(f"Kammer-Alarm (Status {st}) bei ist {ist:+.1f} °C")
        if abs(soll - t) > 0.05:
            raise ProgrammeError(f"Kammer-Sollwert wurde auf {soll:+.1f} °C geändert")
        readings = [(x, v) for x, v in readings if now - x <= 600] + [(now, ist)]
        inside = abs(ist - t) <= a.toleranz
        if inside and since is None:
            since = now
            what = "Messung" if s["zyklen"] else "Ende der Haltezeit"
            journal(f"{t:+g} °C erreicht (ist {ist:+.1f}), {what} um {hm(now + hold * 60)}, wenn stabil")
            if not told_reached and hold >= 5:     # once per step; short holds are not worth a message
                notify(f"{t:+g} °C erreicht", f"{what} um {hm(now + hold * 60)}, wenn die Kammer "
                       f"±{a.toleranz:g} K hält", a)
                told_reached = True
        elif not inside and since is not None:
            journal(f"Toleranz verlassen (ist {ist:+.1f} °C), Haltezeit beginnt neu")
            since = None
        line = f"{hm(now)}  ist {ist:+.1f} °C, soll {soll:+.1f} °C"
        if since is None:
            r = rate_per_min(readings)
            if r and (t - ist) * r > 0:
                line += f", {r:+.2f} K/min, erreicht ca. {hm(now + (t - ist) / r * 60)}"
        else:
            rest = since + hold * 60 - now
            line += f", stabil seit {(now - since) / 60:.0f} min, noch {max(rest, 0) / 60:.0f} min"
            if rest <= 0:
                print(line, flush=True)
                return
        print(line, flush=True)
        time.sleep(a.intervall)


def measure(a, port, run, s, journal):
    """One logger step; returns the summary lines. Raises ProgrammeError if the step failed."""
    cmd = [sys.executable, "-u", str(LOGGER), "--laeufe", str(a.laeufe), "stufe", f"{s['soll']:g}",
           "--lauf", str(run), "--port", port, "--netzteil", a.netzteil, "--netzteil-baud", str(a.netzteil_baud),
           "--keine-bemerkung", "--zyklen", str(s["zyklen"]), "--ein", f"{s['ein']:g}", "--aus", str(s["aus"]),
           "--strom-dicht", f"{a.strom_dicht:g}"]
    if s["tag"]:
        cmd += ["--tag", s["tag"]]
    if a.kein_push:
        cmd.append("--kein-push")
    journal(f"Messung {step_name(s)} beginnt: {s['zyklen']} × {s['ein']:g} s ein / {s['aus']} s aus, "
            f"ca. {step_minutes(s):.0f} min")
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
        raise ProgrammeError(f"Messung {step_name(s)} fehlgeschlagen (Code {code}): {tail[0]}")
    return summary


def show_status(argv):
    """Running programme (PID), latest events and chamber readings; changes nothing."""
    ap = argparse.ArgumentParser(prog="programm status", description="Stand des Messprogramms, ändert nichts")
    ap.add_argument("--zeilen", type=int, default=12, help="letzte Ereigniszeilen (Standard 12)")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port-kammer", dest="kammer_port", type=int, default=PORT)
    ap.add_argument("--laeufe", type=Path, default=RUNS_DIR, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    out = subprocess.run(["pgrep", "-f", "skripte/messprogramm.py"], capture_output=True, text=True).stdout.split()
    pids = [p for p in out if p != str(os.getpid()) and " status" not in
            subprocess.run(["ps", "-p", p, "-o", "args="], capture_output=True, text=True).stdout]
    print(f"Messprogramm läuft (PID {', '.join(pids)})" if pids else "Kein Messprogramm läuft.")
    journals = sorted(a.laeufe.glob("*/*_programm_*.txt"), key=lambda p: p.stat().st_mtime)
    if journals:
        j = journals[-1]
        lines = j.read_text(errors="replace").splitlines()
        age = (time.time() - j.stat().st_mtime) / 60
        print(f"\n{j.parent.name}/{j.name} (letzter Eintrag vor {age:.0f} min):")
        if len(lines) > a.zeilen:
            print("  " + lines[0])
            print("  …")
        for line in lines[-a.zeilen:]:
            print("  " + line)
        klima = sorted(j.parent.glob("*_klima_T*.txt"), key=lambda p: p.stat().st_mtime)
        if klima:
            rows = [l.split("\t") for l in klima[-1].read_text(errors="replace").splitlines() if not l.startswith("#")]
            good = [r for r in rows if len(r) >= 3 and r[1]]
            if good:
                print(f"\nLetzte Kammerabfrage {good[-1][0][11:19]}: ist {good[-1][1]} °C, soll {good[-1][2]} °C")
            if rows and len(rows[-1]) >= 5 and not rows[-1][1]:
                print(f"Letzter Fehler {rows[-1][0][11:19]}: {rows[-1][4]}")
    else:
        print("Kein Programm-Log gefunden.")
    try:
        k = Chamber(a.host, a.kammer_port)
        print(f"Kammer jetzt: ist {k.actual():+.1f} °C, soll {k.setpoint():+.1f} °C, {running_text(k.status())}")
    except SimServError as e:
        print(f"Kammer nicht abfragbar: {e}")


def main():
    if sys.argv[1:2] == ["status"]:
        return show_status(sys.argv[2:])
    cli_steps, argv = split_steps(sys.argv[1:])
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                 usage="%(prog)s TEMPERATUR[:tag] ... [Optionen]  |  %(prog)s --plan DATEI [Optionen]")
    ap.add_argument("--plan", help="Plan-Datei mit Werten je Stufe (Format oben)")
    ap.add_argument("--stabil", type=float, default=30, help="Minuten innerhalb der Toleranz vor jeder Messung (Standard 30)")
    ap.add_argument("--toleranz", type=float, default=1.0, help="K um den Sollwert (Standard 1)")
    ap.add_argument("--max-warten", type=float, default=6, help="Stunden je Stufe bis zum Abbruch (Standard 6)")
    ap.add_argument("--netz-geduld", type=float, default=15,
                    help="Minuten, die Netzfehler beim Setzen des Sollwerts wiederholt werden (Standard 15)")
    ap.add_argument("--intervall", type=float, default=30, help="Sekunden zwischen Kammerabfragen (Standard 30)")
    ap.add_argument("--zyklen", type=int, default=3)
    ap.add_argument("--ein", type=float, default=20, help="Sekunden Netzteil ein je Zyklus (Standard 20)")
    ap.add_argument("--aus", default="10", help="Sekunden Netzteil aus je Zyklus (Standard 10), auch Liste 10,20,40")
    ap.add_argument("--strom-dicht", type=float, default=12,
                    help="die ersten so viele Sekunden jeder EIN-Phase Strom alle 0,5 s lesen (Standard 12)")
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
    try:
        parse_off_times(a.aus)
    except ValueError:
        ap.error(f"--aus: keine Zahl(en): {a.aus}")
    if a.plan and cli_steps:
        ap.error("entweder Temperaturen oder --plan, nicht beides")
    try:
        steps = (read_plan(a.plan, a) if a.plan else
                 [{"soll": t, "tag": tag, "stabil": a.stabil, "zyklen": a.zyklen, "ein": a.ein,
                   "aus": a.aus, "feuchte": None} for t, tag in cli_steps])
    except (ProgrammeError, OSError) as e:
        sys.exit(str(e))
    if not steps:
        ap.error("keine Temperaturen angegeben, z. B.: ./programm 20 -40 -70 20")
    bad = [s["soll"] for s in steps if not T_MIN <= s["soll"] <= T_MAX]
    if bad:
        sys.exit(f"außerhalb {T_MIN:+g} … {T_MAX:+g} °C: {', '.join(f'{t:+g}' for t in bad)}")
    stop_on_sigterm()
    k = Chamber(a.host, a.kammer_port)

    try:
        port = preflight(a, k)
    except ProgrammeError as e:
        sys.exit(f"Nicht gestartet: {e}")
    plan = "  ".join(f"{s['soll']:+g}" + (f":{s['tag']}" if s["tag"] else "") for s in steps)
    print(f"\nProgramm ({len(steps)} Stufen, Toleranz ±{a.toleranz:g} K, Netzteil 5,00 V automatisch"
          + ("" if a.kein_push else ", Push nach jeder Stufe") + "):")
    for i, s in enumerate(steps, 1):
        what = (f"{s['zyklen']} × {s['ein']:g} s ein / {s['aus']} s aus, Messung ca. {step_minutes(s):.0f} min"
                if s["zyklen"] else "nur halten")
        if s.get("feuchte") is not None:
            what = what + f", Feuchte {s['feuchte']:g} %rF"
        print(f"  {i:2d}. {step_name(s):<22} stabil {s['stabil']:g} min, {what}")
    total = sum(s["stabil"] + step_minutes(s) for s in steps) / 60
    print(f"Summe Haltezeiten und Messungen: {total:.1f} h, dazu die Kammerfahrten")
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
    journal(f"Programm {plan} °C, toleranz ±{a.toleranz:g} K" + (f", Plan {a.plan}" if a.plan else
            f", stabil {a.stabil:g} min, {a.zyklen} × {a.ein:g}/{a.aus} s") + f", PID {os.getpid()}")
    if not a.still:
        print(f"Hinweise aufs Handy über ntfy-Thema {a.ntfy}" if a.ntfy else "Kein ntfy-Thema: nur Mac-Mitteilungen")
    try:
        for i, s in enumerate(steps, 1):
            journal(f"Stufe {i}/{len(steps)}: {step_name(s)}")
            if abs(patiently(a, "Sollwert lesen", k.setpoint) - s["soll"]) > 0.05:
                set_chamber(a, k, s["soll"], journal)
            if s.get("feuchte") is not None:
                set_humidity(a, k, s, journal)
            with open_chamber_log(a, run, s) as log:
                wait_stable(a, k, s, log, journal)
                nxt = f" Weiter mit {step_name(steps[i])}." if i < len(steps) else ""
                if not s["zyklen"]:
                    journal(f"Haltezeit {step_name(s)} beendet")
                    continue
                watch = ChamberWatch(k, log, a.intervall)
                watch.start()
                try:
                    summary = measure(a, port, run, s, journal)
                finally:
                    watch.halt.set()
                    watch.join(timeout=15)
            result = "; ".join(l for l in summary if not l.startswith(("Strom", "Nicht gepusht")))
            notify(f"Stufe {step_name(s)} gemessen", f"{result}{nxt}", a)
        journal("Programm fertig")
        notify("Messprogramm fertig", f"{plan} °C, Kammer-Sollwert bleibt {steps[-1]['soll']:+g} °C", a)
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
