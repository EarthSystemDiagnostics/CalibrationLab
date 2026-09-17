#!/usr/bin/env python3
"""Logger for the MB7574 chamber test: one run folder per chamber session.

    python3 schneehoehensensor/skripte/mb7574_kammerlog.py neu
    python3 schneehoehensensor/skripte/mb7574_kammerlog.py stufe -40
    python3 schneehoehensensor/skripte/mb7574_kammerlog.py stufe -70 --tag 60min
    python3 schneehoehensensor/skripte/mb7574_kammerlog.py abschliessen

Short form inside schneehoehensensor/:  ./kammer neu | ./kammer stufe -40 | ./kammer abschliessen

neu           creates laeufe/kammer_MB7574_<YYYYMMDD-HHMMSS>/ with <stem>_meta.txt
              (persons, serial number, description, code commit).
stufe <T>     one temperature step in the newest run folder: announces the power
              supply cycles, logs every sensor line with a timestamp to
              <stem>_T<T>[_<tag>]_<HHMMSS>.txt and updates <stem>_zusammenfassung.csv.
abschliessen  writes a notizen.md template; once it is filled in, commits and
              pushes the run folder after confirmation.

neu and stufe commit and push the run folder on their own (only the run folder, never
code), so the data can be analysed elsewhere minutes later; --kein-push skips that.
A failed push stops nothing: the next step or abschliessen pushes again.

Summary per step: median of all values; median of cycle 1, the cold start after the
soak and the basis of the comparison with the +20 C reference; drift = median of the
last cycle minus cycle 1, i.e. self-heating (the off-time does not cool the sensor back).
Reference: the step tagged 'bezug' if there is one, otherwise the first +20 C step without tag.
With --netzteil the EX355P supply is switched by the script (ex355p.py) and the current is read
back several times per on-phase; otherwise the operator switches and types the current.

Procedure: schneehoehensensor/anleitungen/Kammertest_MB7574.md
"""
import argparse
import csv
import datetime as dt
import glob
import os
import re
import signal
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

import serial

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ex355p import EX355P, SupplyError, DEFAULT_PORT as PSU_PORT, DEFAULT_BAUD as PSU_BAUD   # noqa: E402

PROJECT_DIR = Path(__file__).resolve().parent.parent     # schneehoehensensor/
RUNS_DIR = PROJECT_DIR / "laeufe"
EXPERIMENT = "kammer_MB7574"

VALUE = re.compile(r"^R(\d{4})$")                         # distance in mm
STRAY_BYTES = bytes(range(0, 32)) + bytes(range(127, 256))  # control and non-ASCII bytes
CONTROL_CHARS = "".join(map(chr, range(32)))
NO_ECHO = 5000            # R5000 = no target in the beam
NEAR = 500                # R0500 = smallest reported distance, closer echoes (datasheet 12593)
MIN_VALUES_PER_CYCLE = 3
LIMIT_MEDIAN_PCT = 2.0    # median of cycle 1 against the reference step
LIMIT_CURRENT_PCT = 20.0
CURRENT_FROM_S = 10.0     # strom_mA: median of the supply readings from this time after switching on

# median_z1_mm and drift_mm appended 2026-09-14; since then abw_ref_pct compares medians of cycle 1.
# n_500 appended 2026-09-17; since then R0500 no longer counts as a value (before: in n_werte).
SUMMARY_COLUMNS = ["start", "soll_C", "tag", "zyklen", "zyklen_mit_daten", "zyklen_mit_kopfzeile",
                   "n_werte", "n_5000", "n_ungueltig", "median_mm", "min_mm", "max_mm",
                   "abw_ref_pct", "strom_mA", "bemerkung", "datei", "median_z1_mm", "drift_mm", "n_500"]

NOTES_TEMPLATE = ("# Notizen {name}\n\n"
                  "Laborbuch: LB Schneehöhensensor 1, S. \n\n"
                  "Abweichungen von der Anleitung:\n\n"
                  "Auffälligkeiten:\n\n"
                  "Ergebnis:\n")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def git_state(path):
    """(commit, dirty) of the code in `path`, 'unknown' without git.
    Run folders do not count as dirty. Same logic as kalibrierung/calibration_log.py."""
    def git(*args):
        return subprocess.run(["git", "-C", str(path), *args], capture_output=True,
                              text=True, timeout=5).stdout.strip()
    try:
        commit = git("rev-parse", "HEAD")
        if not commit:
            return "unknown", "unknown"
        dirty = git("status", "--porcelain", "--untracked-files=no", "--", ".", ":(exclude)laeufe")
        return commit, ("yes" if dirty else "no")
    except (OSError, subprocess.SubprocessError):
        return "unknown", "unknown"


def stop_on_sigterm():
    """kill (SIGTERM) and Ctrl-C both end the script through KeyboardInterrupt, so the finally
    blocks switch the supply off. SIGINT is re-armed too: a process started in the background
    by a non-interactive shell inherits SIGINT as ignored. Further signals are ignored, so a
    second Ctrl-C cannot interrupt the switching off."""
    def interrupt(signum, frame):
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)


def push_run(run, message):
    """Commit the run folder, and nothing else, and push. True on success, never exits.
    A rejected push (someone pushed in between) is retried once after pull --rebase."""
    env = os.environ | {"GIT_TERMINAL_PROMPT": "0"}
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=15")

    def git(*args):
        try:
            return subprocess.run(["git", "-C", str(run), *args], capture_output=True,
                                  text=True, timeout=90, env=env)
        except (OSError, subprocess.SubprocessError) as e:
            return subprocess.CompletedProcess(args, 1, "", str(e))

    def failed(step, r):
        detail = (r.stderr or r.stdout).strip().splitlines()
        print(f"Nicht gepusht ({step}: {detail[-1] if detail else 'Fehler'}). "
              "Die Daten liegen lokal; die nächste Stufe oder ./kammer abschliessen pusht erneut.")
        return False

    if git("rev-parse", "--is-inside-work-tree").returncode:
        print("Nicht gepusht: der Laufordner liegt in keinem git-Repo.")
        return False
    r = git("add", "--", ".")
    if r.returncode:
        return failed("git add", r)
    if git("diff", "--cached", "--quiet", "--", ".").returncode:
        r = git("commit", "-m", message, "--", ".")
        if r.returncode:
            return failed("git commit", r)
    r = git("push")
    if r.returncode:
        pull = git("pull", "--rebase", "--autostash")
        if pull.returncode:
            git("rebase", "--abort")
            return failed("git pull --rebase", pull)
        r = git("push")
        if r.returncode:
            return failed("git push", r)
    print("Laufordner eingecheckt und gepusht.")
    return True


def find_port():
    """Sensor port: the FTDI TTL-232R cable if present, otherwise the only /dev/cu.usbserial*
    that is not one of the two channels of the supply's Delock adapter."""
    from serial.tools import list_ports
    ttl = sorted(p.device for p in list_ports.comports()
                 if p.device.startswith("/dev/cu.") and "TTL232R" in f"{p.product} {p.description}")
    ports = ttl if len(ttl) == 1 else sorted(p for p in glob.glob("/dev/cu.usbserial*")
                                             if not p.startswith(PSU_PORT[:-1]))
    if len(ports) != 1:
        sys.exit(f"Sensor-Port nicht eindeutig ({ports or 'keiner gefunden'}), mit --port angeben")
    return ports[0]


def port_users(port):
    """Other processes holding the serial port open, as 'name (PID n)'. Two programs on one
    port split the byte stream between them, so lines go missing. Empty if lsof is unavailable."""
    try:
        out = subprocess.run(["lsof", "-t", port], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    users = []
    for pid in (int(x) for x in out.split() if x.isdigit()):
        if pid != os.getpid():
            name = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True).stdout.strip()
            users.append(f"{Path(name).name or '?'} (PID {pid})")
    return users


def pick_run(a):
    """Run folder from --lauf (name or path), otherwise the newest one."""
    if a.lauf:
        p = Path(a.lauf)
        p = p if p.is_dir() else a.laeufe / a.lauf
        if not p.is_dir():
            sys.exit(f"Laufordner nicht gefunden: {a.lauf}")
        return p
    runs = sorted(p for p in a.laeufe.glob(f"{EXPERIMENT}_*") if p.is_dir())
    if not runs:
        sys.exit(f"Kein Laufordner in {a.laeufe}. Erst: ./kammer neu")
    return runs[-1]


def number(text):
    try:
        return float(str(text).replace(",", "."))
    except ValueError:
        return None


def fmt(x, spec="g"):
    return "" if x is None else format(x, spec)


class SensorReader(threading.Thread):
    """Reads the sensor, splits at CR and logs every line with a timestamp."""

    def __init__(self, ser, log):
        super().__init__(daemon=True)
        self.ser, self.log = ser, log
        self.t0 = time.time()
        self.cycle, self.phase = 0, "-"
        self.lines = []          # (cycle, ascii_only, text)
        self.last = ""
        self.halt = threading.Event()
        self.lock = threading.Lock()

    def note(self, cycle, phase, text):
        now = time.time()
        with self.lock:
            self.log.write(f"{dt.datetime.fromtimestamp(now).isoformat(timespec='milliseconds')}\t"
                           f"{now - self.t0:.3f}\t{cycle}\t{phase}\t{text}\n")
            self.log.flush()

    def run(self):
        buf = b""
        while not self.halt.is_set():
            buf += self.ser.read(64)
            *done, buf = re.split(rb"[\r\n]", buf)
            for raw in filter(None, done):
                self.note(self.cycle, self.phase, raw.decode("ascii", "backslashreplace"))   # log stays raw
                # Switching the supply leaves stray bytes in front of the next line
                # (terminal shows '.SCXL-MaxSonar-WRS'); they do not make the line invalid.
                clean = raw.lstrip(STRAY_BYTES)
                if clean:
                    text = clean.decode("ascii", "backslashreplace")
                    self.lines.append((self.cycle, all(32 <= b < 127 for b in clean), text))
                    self.last = text


def parse_off_times(text):
    return [float(x) for x in str(text).split(",") if x.strip()]


def off_times_arg(text):
    try:
        times = parse_off_times(text)
    except ValueError:
        times = []
    if not times or min(times) < 0:
        raise argparse.ArgumentTypeError(f"Aus-Zeit(en) in s, z. B. 10 oder 10,20,40: {text!r}")
    return str(text)


def countdown(seconds, label, reader, sample=None, every=6.0, dense_until=0.0, dense_every=0.5):
    """Wait with a status line. sample(elapsed) runs every dense_every seconds during the first
    dense_until seconds of the phase, then every `every` seconds; without a dense window the
    first call is 2 s into the phase (half-way if shorter)."""
    start = time.time()
    end = start + seconds
    next_sample = start + (min(dense_every, seconds / 2) if dense_until > 0 else min(2.0, seconds / 2))
    while (rest := end - time.time()) > 0:
        if sample and time.time() >= next_sample and rest > 0.3:
            elapsed = time.time() - start
            sample(elapsed)
            next_sample = time.time() + (dense_every if elapsed < dense_until else every)
        print(f"\r  {label}: noch {rest:3.0f} s   letzte Zeile: {reader.last[:20]:<20}", end="", flush=True)
        time.sleep(min(0.25, rest))
    print()


def evaluate(lines, cycles):
    values, n_no_echo, invalid, with_data, with_header, per_cycle = [], 0, 0, 0, 0, []
    n_near = 0
    for k in range(1, cycles + 1):
        values_k, header = [], False
        for _, ascii_only, text in (x for x in lines if x[0] == k):
            m = VALUE.match(text)
            if m:
                mm = int(m.group(1))
                if mm >= NO_ECHO:
                    n_no_echo += 1
                elif mm <= NEAR:
                    n_near += 1
                else:
                    values_k.append(mm)
            elif not ascii_only or re.match(r"^R\d", text):
                invalid += 1
            else:
                header = True
        with_header += header
        with_data += len(values_k) >= MIN_VALUES_PER_CYCLE
        values += values_k
        per_cycle.append(values_k)
    return values, n_no_echo, n_near, invalid, with_data, with_header, per_cycle


def cycle_stats(per_cycle):
    """(median of cycle 1, median of the last cycle minus cycle 1); None where undefined."""
    first = statistics.median(per_cycle[0]) if per_cycle and per_cycle[0] else None
    if first is None or len(per_cycle) < 2 or not per_cycle[-1]:
        return first, None
    return first, statistics.median(per_cycle[-1]) - first


def cycles_from_log(path):
    """Valid distance values per cycle, re-read from a step log."""
    per_cycle, n_cycles = {}, 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if line.startswith("#") or len(parts) < 5 or not parts[2].isdigit():
                continue
            k = int(parts[2])
            n_cycles = max(n_cycles, k)
            m = VALUE.match(parts[4].strip(CONTROL_CHARS))
            if k and m and NEAR < int(m.group(1)) < NO_ECHO:
                per_cycle.setdefault(k, []).append(int(m.group(1)))
    return [per_cycle.get(k, []) for k in range(1, n_cycles + 1)]


def read_summary(csv_path):
    """Summary rows with the current columns. Rows written by an older logger version
    get median_z1_mm and drift_mm recomputed from their step log."""
    if not csv_path.exists():
        return []
    with open(csv_path, newline="") as f:
        rows = [{k: row.get(k) or "" for k in SUMMARY_COLUMNS} for row in csv.DictReader(f)]
    for row in rows:
        log = csv_path.parent / row["datei"]
        if not row["median_z1_mm"] and row["datei"] and log.exists():
            first, drift = cycle_stats(cycles_from_log(log))
            row["median_z1_mm"], row["drift_mm"] = fmt(first), fmt(drift)
    return rows


def update_summary(csv_path, new_row):
    """Append a step, recompute every deviation against the reference (step tagged 'bezug',
    else the first +20 C step without tag; median of cycle 1) and rewrite the summary.
    Returns the reference row."""
    rows = read_summary(csv_path) + [new_row]
    ref = (next((r for r in rows if r["tag"] == "bezug" and r["median_z1_mm"]), None)
           or next((r for r in rows if number(r["soll_C"]) == 20 and not r["tag"] and r["median_z1_mm"]), None))
    for r in rows:
        r["abw_ref_pct"] = (fmt(100 * (float(r["median_z1_mm"]) / float(ref["median_z1_mm"]) - 1), ".2f")
                            if ref and r["median_z1_mm"] else "")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SUMMARY_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return ref


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def cmd_new(a):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run = a.laeufe / f"{EXPERIMENT}_{stamp}"
    run.mkdir(parents=True)
    persons = a.personen if a.personen is not None else input("Personen: ").strip()
    serial_no = a.seriennummer if a.seriennummer is not None else input("Seriennummer MB7574: ").strip()
    description = (a.beschreibung if a.beschreibung is not None
                   else input("Beschreibung (Kammer, Ziel, Besonderheiten): ").strip())
    commit, dirty = git_state(PROJECT_DIR)
    with open(run / f"{run.name}_meta.txt", "w") as m:
        m.write(f"Experiment       : {EXPERIMENT}\n"
                f"Run stamp        : {stamp}\n"
                f"Start (PC time)  : {dt.datetime.now().isoformat(timespec='seconds')}\n"
                f"Persons          : {persons}\n"
                f"Sensor           : MaxBotix MB7574\n"
                f"Serial number    : {serial_no}\n"
                f"Description      : {description}\n"
                f"Code commit      : {commit}\n"
                f"Uncommitted code : {dirty}\n")
    print(f"\nLaufordner angelegt: {run}\nIns Laborbuch: {run.name}")
    if dirty != "no":
        print("WARNUNG: Der Code hat nicht eingecheckte Änderungen; der Lauf ist keinem Code-Stand eindeutig zuzuordnen.")
    if not a.kein_push:
        push_run(run, f"schneehoehensensor: Lauf {run.name} angelegt")


def cmd_step(a):
    run = pick_run(a)
    port = a.port or find_port()
    for p in filter(None, (port, a.netzteil)):
        users = port_users(p)
        if users:
            sys.exit(f"Port {p} ist schon geöffnet von {', '.join(users)}. "
                     "Dort trennen (CoolTerm: Disconnect), dann erneut starten.")
    psu, ident = None, ""
    if a.netzteil:
        try:
            psu = EX355P(a.netzteil, a.netzteil_baud)
            ident = psu.identify()
            if "EX355P" not in ident:
                raise SupplyError(f"unerwartetes Gerät am Netzteil-Port {a.netzteil}: {ident}")
            psu.setup()
        except SupplyError as e:
            if psu:
                psu.close()
            sys.exit(str(e))
    start = dt.datetime.now()
    log_path = run / ("_".join(filter(None, [run.name, f"T{a.soll:+g}", a.tag, start.strftime("%H%M%S")])) + ".txt")
    csv_path = run / f"{run.name}_zusammenfassung.csv"

    with serial.Serial(port, 9600, timeout=0.2) as ser, open(log_path, "w") as log:
        log.write(f"# MB7574 Kammertest, Lauf {run.name}, Soll {a.soll:+g} °C, Tag '{a.tag}', Port {port}, "
                  f"{a.zyklen} Zyklen je {a.ein:g} s ein / {a.aus} s aus"
                  + (f", Netzteil automatisch {a.netzteil} ({ident}), 5.00 V / 0.15 A" if psu else "") + "\n"
                  "# zeit\tt_s\tzyklus\tphase\tzeile\n")
        reader = SensorReader(ser, log)
        reader.start()
        amps = []                   # (seconds since on, amps)
        off_times = parse_off_times(a.aus)

        def sample(elapsed):
            try:
                volts, a_now = psu.measure()
            except SupplyError as e:
                reader.note(reader.cycle, reader.phase, f"# Netzteil: {e}")
                if "über" in str(e):
                    raise          # overvoltage: output is already off, stop the step
                return
            amps.append((elapsed, a_now))
            reader.note(reader.cycle, reader.phase, f"# Netzteil {volts:.1f} V {a_now * 1000:.0f} mA")

        print(f"Lauf: {run.name}\nLog:  {log_path.name}\n"
              + ("Netzteil wird automatisch geschaltet." if psu else "Netzteil AUS lassen bis zur Ansage."))
        try:
            time.sleep(2)
            for k in range(1, a.zyklen + 1):
                reader.cycle, reader.phase = k, "EIN"
                if psu:
                    psu.on()
                    reader.note(k, "EIN", "# Netzteil EIN (automatisch)")
                    print(f"\nZyklus {k}/{a.zyklen}: Netzteil EIN (automatisch)")
                else:
                    reader.note(k, "EIN", "# Ansage Netzteil EIN")
                    print(f"\a\nZyklus {k}/{a.zyklen}: Netzteil EIN" + ("   (Strom ablesen)" if k == 1 else ""))
                countdown(a.ein, "EIN", reader, sample if psu else None, dense_until=a.strom_dicht)
                reader.phase = "AUS"
                if psu:
                    psu.off()
                    reader.note(k, "AUS", "# Netzteil AUS (automatisch)")
                    print(f"Zyklus {k}/{a.zyklen}: Netzteil AUS (automatisch)")
                else:
                    reader.note(k, "AUS", "# Ansage Netzteil AUS")
                    print(f"\aZyklus {k}/{a.zyklen}: Netzteil AUS")
                if k < a.zyklen:
                    countdown(off_times[(k - 1) % len(off_times)], "AUS", reader)
                else:
                    time.sleep(1)       # last cycle: no off-time needed, only catch the final line
        finally:
            if psu:
                try:
                    psu.off()
                finally:
                    psu.close()
        reader.halt.set()
        reader.join()
        if psu:
            # the current changes about 8 s after switching on (60 -> 100 mA at -50 C): median from 10 s on
            late = [x for t_on, x in amps if t_on >= CURRENT_FROM_S] or [x for _, x in amps]
            current_text = fmt(1000 * statistics.median(late) if late else None)
            print(f"\nStrom laut Netzteil: {current_text or '-'} mA (Median aus {len(late)} Ablesungen "
                  f"ab {CURRENT_FROM_S:g} s nach EIN, Auflösung 10 mA)")
            remark = "" if a.keine_bemerkung else input("Bemerkung: ").strip()
        else:
            current_text = input("\nStrom während EIN in mA (leer = nicht abgelesen): ").strip()
            remark = input("Bemerkung: ").strip()
        reader.note(0, "-", f"# Strom {current_text or '-'} mA; Bemerkung: {remark}")

    values, n_no_echo, n_near, invalid, with_data, with_header, per_cycle = evaluate(reader.lines, a.zyklen)
    median = statistics.median(values) if values else None
    median_z1, drift = cycle_stats(per_cycle)
    current = number(current_text) if current_text else None
    row = dict(zip(SUMMARY_COLUMNS, [
        start.isoformat(timespec="seconds"), f"{a.soll:g}", a.tag, a.zyklen, with_data, with_header,
        len(values), n_no_echo, invalid, fmt(median), fmt(min(values) if values else None),
        fmt(max(values) if values else None), "", fmt(current), remark, log_path.name,
        fmt(median_z1), fmt(drift), n_near]))
    ref = update_summary(csv_path, row)
    dev = number(row["abw_ref_pct"]) if row["abw_ref_pct"] else None

    flags = []
    if with_data < a.zyklen:
        flags.append(f"nur {with_data}/{a.zyklen} Zyklen mit Daten")
    if with_header < a.zyklen:
        flags.append(f"nur {with_header}/{a.zyklen} Zyklen mit Kopfzeile")
    if n_no_echo:
        flags.append(f"{n_no_echo} × R5000")
    if n_near:
        flags.append(f"{n_near} × R0500 (Echo näher als 50 cm)")
    if invalid:
        flags.append(f"{invalid} ungültige Zeilen")
    if dev is not None and abs(dev) > LIMIT_MEDIAN_PCT:
        flags.append(f"Zyklus 1 {dev:+.1f} % gegen +20 °C")
    if ref and ref["strom_mA"] and current is not None:
        dev_current = 100 * (current / float(ref["strom_mA"]) - 1)
        if abs(dev_current) > LIMIT_CURRENT_PCT:
            flags.append(f"Strom {dev_current:+.0f} % gegen +20 °C")

    print(f"\nSoll {a.soll:+g} °C: Zyklen mit Daten {with_data}/{a.zyklen}, mit Kopfzeile {with_header}/{a.zyklen}")
    print(f"Werte {len(values)} (R5000: {n_no_echo}, R0500: {n_near}, ungültig: {invalid})")
    if values:
        print(f"Median alle Werte {median:g} mm (min {min(values)}, max {max(values)})")
    if median_z1 is not None:
        print(f"Median Zyklus 1 (Kaltstart) {median_z1:g} mm"
              + (f", Anstieg bis Zyklus {len(per_cycle)}: {drift:+g} mm" if drift is not None else "")
              + (f", gegen +20 °C {dev:+.1f} %" if dev is not None else ""))
    print("AUFFÄLLIG: " + "; ".join(flags) if flags else "keine Auffälligkeit")
    if not a.kein_push:
        push_run(run, f"schneehoehensensor: Lauf {run.name} Stufe {a.soll:+g} C" + (f" {a.tag}" if a.tag else ""))


def cmd_close(a):
    run = pick_run(a)
    notes = run / "notizen.md"
    template = NOTES_TEMPLATE.format(name=run.name)
    if not notes.exists():
        notes.write_text(template)
        print(f"Vorlage angelegt: {notes}\n"
              "Ausfüllen, Fotos nach fotos/ und Laborbuch-Scans nach scans/ legen, dann erneut: abschliessen")
        return
    if notes.read_text() == template:
        print(f"{notes.name} ist noch nicht ausgefüllt. Erst ausfüllen, dann erneut: abschliessen")
        return
    skipped = [p for p in run.rglob("*") if p.suffix.lower() in (".heic", ".mov")]
    if skipped:
        print("Wird nicht eingecheckt (HEIC/Video): " + ", ".join(p.name for p in skipped))
        print("Fotos umwandeln, z. B.: sips -s format jpeg -Z 1600 BILD.HEIC --out BILD.jpg")
        return
    files = sorted(str(p.relative_to(run)) for p in run.rglob("*") if p.is_file() and not p.name.startswith("."))
    print(f"{run.name}: {len(files)} Dateien")
    for name in files:
        print("  " + name)
    if input("\nEinchecken und pushen? [j/N] ").strip().lower() != "j":
        print("Nicht eingecheckt.")
        return
    if not push_run(run, f"schneehoehensensor: Lauf {run.name} abgeschlossen"):
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--laeufe", type=Path, default=RUNS_DIR, help=argparse.SUPPRESS)   # tests only
    sub = ap.add_subparsers(dest="command", required=True)

    no_push = argparse.ArgumentParser(add_help=False)
    no_push.add_argument("--kein-push", action="store_true", help="Laufordner nicht selbst einchecken und pushen")

    nw = sub.add_parser("neu", help="Laufordner anlegen", parents=[no_push])
    nw.add_argument("--personen", help="ohne Rückfrage")
    nw.add_argument("--seriennummer", help="ohne Rückfrage")
    nw.add_argument("--beschreibung", help="ohne Rückfrage")

    st = sub.add_parser("stufe", help="eine Temperaturstufe messen", parents=[no_push])
    st.add_argument("soll", type=float, help="Solltemperatur der Kammer in °C")
    st.add_argument("--tag", default="", help="Zusatz im Dateinamen, z. B. tisch, 60min, wdh, ende")
    st.add_argument("--zyklen", type=int, default=3)
    st.add_argument("--ein", type=float, default=20, help="Sekunden Netzteil ein (Standard 20)")
    st.add_argument("--aus", default="10", type=off_times_arg,
                    help="Sekunden Netzteil aus (Standard 10); Liste wie 10,20,40 gilt der Reihe nach je Zyklus")
    st.add_argument("--strom-dicht", type=float, default=12,
                    help="die ersten so viele Sekunden jeder EIN-Phase Strom alle 0,5 s lesen (Standard 12, 0 = aus)")
    st.add_argument("--port", help="Sensor-Port, Standard: das TTL-232R-Kabel")
    st.add_argument("--netzteil", nargs="?", const=PSU_PORT,
                    help=f"Netzteil EX355P selbst schalten und Strom lesen (Port, Standard {PSU_PORT})")
    st.add_argument("--netzteil-baud", type=int, default=PSU_BAUD, help=argparse.SUPPRESS)
    st.add_argument("--keine-bemerkung", action="store_true", help="nicht nach einer Bemerkung fragen")
    st.add_argument("--lauf", help="Laufordner (Name oder Pfad), Standard: der neueste")

    ab = sub.add_parser("abschliessen", help="Notizen anlegen, Laufordner einchecken und pushen")
    ab.add_argument("--lauf", help="Laufordner (Name oder Pfad), Standard: der neueste")

    a = ap.parse_args()
    stop_on_sigterm()
    try:
        {"neu": cmd_new, "stufe": cmd_step, "abschliessen": cmd_close}[a.command](a)
    except SupplyError as e:
        sys.exit(f"Netzteil: {e} (Ausgang wurde ausgeschaltet)")
    except KeyboardInterrupt:
        print("\nAbgebrochen" + (", Netzteil ausgeschaltet." if getattr(a, "netzteil", None) else "."), file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
