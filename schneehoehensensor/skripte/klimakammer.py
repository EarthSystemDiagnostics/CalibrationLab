#!/usr/bin/env python3
"""Climate chamber Weiss ClimeEvent (lab name "das Bad") over SimServ, TCP port 2049.

    ./klima status               read temperature, setpoint and status (changes nothing)
    ./klima soll -40             set the temperature setpoint (asks for confirmation)
    ./klima stufe -40            set the setpoint, wait until reached, hold 30 min, notify
    ./klima warten -40           like stufe, without touching the setpoint

The chamber must be reachable from this Mac (LAN adapter 172.168.225.10/24, chamber
172.168.225.202, no gateway). SimServ: fields separated by byte 0xB6 (latin-1),
'command, chamber 1, parameters' + CR; answer '1', fields + CRLF; one TCP connection
per command. Read: 11004 actual, 11002 setpoint (control variable 1 = temperature,
2 = humidity), 10012 status (1 not running, 3 running, +4 warning, +8 alarm), 99997 info.
Error answers are negative codes: -5 unknown command, -6 bad parameters, -8 read failure.
Parameter layout as in github.com/IzaakWN/ClimateChamberMonitor (chamber_commands.py).
Write: 11001 setpoint, 14001 manual mode on. The write commands were first used by
this script (17.09.2026): check with 'status' before relying on them.

While waiting, chamber readings go to the newest run folder of schneehoehensensor
as <stem>_klima_T<T>_<HHMMSS>.txt. Push notifications via ntfy.sh go to the topic in
~/.klima_ntfy (one line, not in the repo) or $KLIMA_NTFY; --ntfy overrides it. They are sent
with curl, which uses the system certificates (python.org builds of Python lack them).
"""
import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mb7574_kammerlog import RUNS_DIR, pick_run   # noqa: E402

HOST = os.environ.get("KLIMA_HOST", "172.168.225.202")
PORT = int(os.environ.get("KLIMA_PORT", "2049"))
SEP = "\xb6"
T_MIN, T_MAX = -72.0, 40.0          # chamber limit -72 C; upper bound kept low for these tests
STATUS_RUNNING, STATUS_WARNING, STATUS_ALARM = 2, 4, 8
ERRORS = {-5: "unbekannter Befehl", -6: "falsche Parameter", -8: "Lesefehler"}
NTFY_FILE = Path.home() / ".klima_ntfy"


class SimServError(Exception):
    pass


class Chamber:
    def __init__(self, host, port, timeout=5.0):
        self.host, self.port, self.timeout = host, port, timeout

    def query(self, cmd, *params):
        msg = SEP.join([str(cmd), "1", *map(str, params)]) + "\r"
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as s:
                s.sendall(msg.encode("latin-1"))
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = s.recv(1024)
                    if not chunk:
                        break
                    buf += chunk
        except OSError as e:
            raise SimServError(f"keine Verbindung zur Kammer {self.host}:{self.port} ({e})") from e
        fields = buf.decode("latin-1").strip().split(SEP)
        if not fields or fields[0] != "1":
            try:
                code = int(fields[0])
            except (ValueError, IndexError):
                raise SimServError(f"Befehl {cmd}: unerwartete Antwort {buf!r}") from None
            raise SimServError(f"Befehl {cmd}: Fehler {code} ({ERRORS.get(code, 'unbekannt')})")
        return fields[1:]

    def value(self, cmd, *params):
        return float(self.query(cmd, *params)[0].replace(",", "."))

    def actual(self):
        return self.value(11004, 1)

    def setpoint(self):
        return self.value(11002, 1)

    def status(self):
        return int(self.value(10012))

    def info(self):
        """Chamber info text, or None: some controllers answer 99997 with -8."""
        for params in ((), (1,)):
            try:
                return " ".join(self.query(99997, *params))
            except SimServError:
                pass
        return None

    def set_setpoint(self, t):
        self.query(11001, 1, f"{t:.1f}")

    def start_manual(self):
        self.query(14001, 1, 1)


def default_ntfy():
    """ntfy topic from $KLIMA_NTFY or ~/.klima_ntfy; None if neither is set."""
    topic = os.environ.get("KLIMA_NTFY", "").strip()
    if topic:
        return topic
    try:
        return NTFY_FILE.read_text().strip() or None
    except OSError:
        return None


def hm(ts):
    return time.strftime("%H:%M", time.localtime(ts))


def running_text(st):
    return (("läuft" if st & STATUS_RUNNING else "läuft nicht")
            + (", Warnung" if st & STATUS_WARNING else "") + (", ALARM" if st & STATUS_ALARM else ""))


def notify(title, text, a):
    """Terminal line with bell; Mac notification with sound; optional push via ntfy.sh."""
    print(f"\a>>> {title}: {text}", flush=True)
    if a.still:
        return
    subprocess.run(["osascript", "-e", f'display notification "{text.replace(chr(34), chr(39))}" '
                    f'with title "{title}" sound name "Glass"'], capture_output=True)
    if a.ntfy:
        try:
            r = subprocess.run(["curl", "-sS", "-m", "10", "-H", f"Title: {title}", "--data-binary", text,
                                f"https://ntfy.sh/{a.ntfy}"], capture_output=True, text=True)
            error = r.stderr.strip() if r.returncode else ""
        except OSError as e:
            error = str(e)
        if error:
            print(f"ntfy nicht erreichbar: {error}")


def rate_per_min(readings):
    """Least-squares slope of (time, temperature) in K/min; None with fewer than 3 points."""
    if len(readings) < 3:
        return None
    n = len(readings)
    mt = sum(t for t, _ in readings) / n
    mv = sum(v for _, v in readings) / n
    den = sum((t - mt) ** 2 for t, _ in readings)
    return None if den == 0 else 60 * sum((t - mt) * (v - mv) for t, v in readings) / den


def show_status(a, k):
    st = k.status()
    info = k.info()
    print(f"Kammer {a.host}" + (f": {info}" if info else ""))
    print(f"Temperatur ist {k.actual():+.1f} °C, soll {k.setpoint():+.1f} °C")
    try:
        print(f"Feuchte ist {k.value(11004, 2):.1f} %rF, soll {k.value(11002, 2):.1f} %rF")
    except (SimServError, ValueError, IndexError):
        pass
    print(f"Status {st}: {running_text(st)}")


def set_setpoint(a, k):
    if not T_MIN <= a.soll <= T_MAX:
        sys.exit(f"Sollwert {a.soll:+g} °C liegt außerhalb {T_MIN:+g} … {T_MAX:+g} °C.")
    old, ist, st = k.setpoint(), k.actual(), k.status()
    print(f"Kammer: ist {ist:+.1f} °C, soll {old:+.1f} °C, {running_text(st)}")
    if not a.ja and input(f"Sollwert auf {a.soll:+g} °C setzen? [j/N] ").strip().lower() != "j":
        sys.exit("Nicht gesetzt.")
    k.set_setpoint(a.soll)
    new = k.setpoint()
    if abs(new - a.soll) > 0.05:
        sys.exit(f"Kammer meldet Sollwert {new:+.1f} °C statt {a.soll:+g} °C. Am Panel prüfen.")
    print(f"Sollwert gesetzt: {new:+.1f} °C")
    if not st & STATUS_RUNNING:
        if a.start:
            k.start_manual()
            time.sleep(2)
            print("Handbetrieb gestartet." if k.status() & STATUS_RUNNING
                  else "WARNUNG: Kammer meldet weiterhin „läuft nicht“. Am Panel prüfen.")
        else:
            print("WARNUNG: Die Kammer läuft nicht. Am Panel starten oder erneut mit --start.")


def open_log(a):
    if a.ohne_log:
        return None
    try:
        run = pick_run(a)
    except SystemExit:
        print("Kein Laufordner gefunden; Kammerwerte werden nicht gespeichert.")
        return None
    path = run / f"{run.name}_klima_T{a.soll:+g}_{time.strftime('%H%M%S')}.txt"
    log = open(path, "w")
    log.write(f"# Klimakammer {a.host}, Soll {a.soll:+g} °C, Toleranz {a.toleranz:g} K, halten {a.halten:g} min\n"
              "# zeit\tist_C\tsoll_C\tstatus\tfehler\n")
    print(f"Kammerwerte: {path.name}")
    return log


def wait_and_hold(a, k):
    log = open_log(a)
    readings, reached, ready, warned, alarm_told, max_dev = [], None, None, False, False, 0.0
    print(f"Warte auf {a.soll:+g} °C (±{a.toleranz:g} K), dann {a.halten:g} min halten. Beenden mit Ctrl-C.")
    if not a.still:
        print(f"Hinweise aufs Handy über ntfy-Thema {a.ntfy}" if a.ntfy
              else f"Keine Hinweise aufs Handy: kein ntfy-Thema in {NTFY_FILE}")
    try:
        while True:
            now = time.time()
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))
            try:
                ist, soll, st = k.actual(), k.setpoint(), k.status()
            except (SimServError, ValueError, IndexError) as e:
                print(f"{hm(now)}  {e}; neuer Versuch in {a.intervall:g} s", flush=True)
                if log:
                    log.write(f"{stamp}\t\t\t\t{e}\n")
                    log.flush()
                time.sleep(a.intervall)
                continue
            if log:
                log.write(f"{stamp}\t{ist:.2f}\t{soll:.2f}\t{st}\t\n")
                log.flush()
            readings = [(t, v) for t, v in readings if now - t <= 600] + [(now, ist)]
            line = f"{hm(now)}  ist {ist:+.1f} °C, soll {soll:+.1f} °C"
            if abs(soll - a.soll) > 0.05:
                line += f" (ACHTUNG: Kammer-Sollwert ist nicht {a.soll:+g} °C)"
            if st & STATUS_ALARM and not alarm_told:
                notify("Kammer-Alarm", f"Alarm an der Klimakammer, Status {st}", a)
                alarm_told = True
            if reached is None and abs(ist - a.soll) <= a.toleranz:
                reached, ready = now, now + a.halten * 60
                notify("Soll erreicht", f"{a.soll:+g} °C erreicht um {hm(now)}, Stufe messen um {hm(ready)}", a)
            if reached is None:
                r = rate_per_min(readings)
                if r and (a.soll - ist) * r > 0:
                    t_reach = now + (a.soll - ist) / r * 60
                    line += f", {r:+.2f} K/min, erreicht ca. {hm(t_reach)}, Stufe ca. {hm(t_reach + a.halten * 60)}"
                print(line, flush=True)
            else:
                max_dev = max(max_dev, abs(ist - a.soll))
                rest = ready - now
                print(line + f", halten noch {max(rest, 0) / 60:.0f} min (Stufe um {hm(ready)})", flush=True)
                if not warned and 0 < rest <= a.vorwarnung * 60:
                    notify("Kammer", f"In {rest / 60:.0f} min Stufe {a.soll:+g} °C messen ({hm(ready)})", a)
                    warned = True
                if rest <= 0:
                    notify("Stufe messen", f"{a.soll:+g} °C seit {a.halten:g} min, größte Abweichung {max_dev:.1f} K. "
                           f"Jetzt: ./kammer stufe {a.soll:g}", a)
                    break
            time.sleep(a.intervall)
    finally:
        if log:
            log.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=HOST, help=f"Kammer, Standard {HOST}")
    ap.add_argument("--port", type=int, default=PORT, help=f"SimServ-Port, Standard {PORT}")
    ap.add_argument("--laeufe", type=Path, default=RUNS_DIR, help=argparse.SUPPRESS)   # tests only
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="Temperatur, Sollwert und Status lesen, ändert nichts")
    for name, text in (("soll", "Sollwert setzen"),
                       ("stufe", "Sollwert setzen, warten, halten, melden"),
                       ("warten", "warten, halten, melden, ohne den Sollwert zu setzen")):
        p = sub.add_parser(name, help=text)
        p.add_argument("soll", type=float, help="Solltemperatur in °C")
        if name in ("soll", "stufe"):
            p.add_argument("--ja", action="store_true", help="ohne Rückfrage setzen")
            p.add_argument("--start", action="store_true", help="Handbetrieb starten, falls die Kammer nicht läuft")
        if name in ("stufe", "warten"):
            p.add_argument("--halten", type=float, default=30, help="Minuten halten nach Erreichen (Standard 30)")
            p.add_argument("--toleranz", type=float, default=1.0, help="erreicht bei |ist - soll| <= Toleranz, K (Standard 1)")
            p.add_argument("--vorwarnung", type=float, default=10, help="Hinweis so viele Minuten vor Ende (Standard 10)")
            p.add_argument("--intervall", type=float, default=30, help="Sekunden zwischen Abfragen (Standard 30)")
            p.add_argument("--ntfy", default=default_ntfy(),
                           help=f"Thema auf ntfy.sh für Hinweise aufs Handy (Standard aus {NTFY_FILE})")
            p.add_argument("--still", action="store_true", help="keine Mac-Mitteilung")
            p.add_argument("--ohne-log", action="store_true", help="Kammerwerte nicht speichern")
            p.add_argument("--lauf", help="Laufordner (Name oder Pfad), Standard: der neueste")
    a = ap.parse_args()
    k = Chamber(a.host, a.port)
    try:
        if a.command == "status":
            show_status(a, k)
        elif a.command == "soll":
            set_setpoint(a, k)
        elif a.command == "stufe":
            set_setpoint(a, k)
            wait_and_hold(a, k)
        else:
            wait_and_hold(a, k)
    except SimServError as e:
        sys.exit(str(e))
    except KeyboardInterrupt:
        sys.exit("\nBeendet. Der Sollwert der Kammer bleibt, wie er ist.")


if __name__ == "__main__":
    main()
