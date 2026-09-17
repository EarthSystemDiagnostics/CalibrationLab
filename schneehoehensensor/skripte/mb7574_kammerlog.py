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

Summary per step: median of all values; median of cycle 1, the cold start after the
soak and the basis of the comparison with the +20 C reference; drift = median of the
last cycle minus cycle 1, i.e. self-heating (the off-time does not cool the sensor back).

Procedure: schneehoehensensor/anleitungen/Kammertest_MB7574.md
"""
import argparse
import csv
import datetime as dt
import glob
import re
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

import serial

PROJECT_DIR = Path(__file__).resolve().parent.parent     # schneehoehensensor/
RUNS_DIR = PROJECT_DIR / "laeufe"
EXPERIMENT = "kammer_MB7574"

VALUE = re.compile(r"^R(\d{4})$")                         # distance in mm
STRAY_BYTES = bytes(range(0, 32)) + bytes(range(127, 256))  # control and non-ASCII bytes
CONTROL_CHARS = "".join(map(chr, range(32)))
NO_ECHO = 5000            # R5000 = no target in the beam
MIN_VALUES_PER_CYCLE = 3
LIMIT_MEDIAN_PCT = 2.0    # median of cycle 1 against the reference step
LIMIT_CURRENT_PCT = 20.0

# median_z1_mm and drift_mm appended 2026-09-14; since then abw_ref_pct compares medians of cycle 1.
SUMMARY_COLUMNS = ["start", "soll_C", "tag", "zyklen", "zyklen_mit_daten", "zyklen_mit_kopfzeile",
                   "n_werte", "n_5000", "n_ungueltig", "median_mm", "min_mm", "max_mm",
                   "abw_ref_pct", "strom_mA", "bemerkung", "datei", "median_z1_mm", "drift_mm"]

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


def find_port():
    ports = sorted(glob.glob("/dev/cu.usbserial*"))
    if len(ports) != 1:
        sys.exit(f"FTDI-Port nicht eindeutig ({ports or 'keiner gefunden'}), mit --port angeben")
    return ports[0]


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


def countdown(seconds, label, reader):
    end = time.time() + seconds
    while (rest := end - time.time()) > 0:
        print(f"\r  {label}: noch {rest:3.0f} s   letzte Zeile: {reader.last[:20]:<20}", end="", flush=True)
        time.sleep(min(0.25, rest))
    print()


def evaluate(lines, cycles):
    values, n_no_echo, invalid, with_data, with_header, per_cycle = [], 0, 0, 0, 0, []
    for k in range(1, cycles + 1):
        values_k, header = [], False
        for _, ascii_only, text in (x for x in lines if x[0] == k):
            m = VALUE.match(text)
            if m:
                mm = int(m.group(1))
                if mm >= NO_ECHO:
                    n_no_echo += 1
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
    return values, n_no_echo, invalid, with_data, with_header, per_cycle


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
            if k and m and int(m.group(1)) < NO_ECHO:
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
    """Append a step, recompute every deviation against the reference (first +20 C step
    without tag, median of cycle 1) and rewrite the summary. Returns the reference row."""
    rows = read_summary(csv_path) + [new_row]
    ref = next((r for r in rows if number(r["soll_C"]) == 20 and not r["tag"] and r["median_z1_mm"]), None)
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
    persons = input("Personen: ").strip()
    serial_no = input("Seriennummer MB7574: ").strip()
    description = input("Beschreibung (Kammer, Ziel, Besonderheiten): ").strip()
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


def cmd_step(a):
    run = pick_run(a)
    port = a.port or find_port()
    start = dt.datetime.now()
    log_path = run / ("_".join(filter(None, [run.name, f"T{a.soll:+g}", a.tag, start.strftime("%H%M%S")])) + ".txt")
    csv_path = run / f"{run.name}_zusammenfassung.csv"

    with serial.Serial(port, 9600, timeout=0.2) as ser, open(log_path, "w") as log:
        log.write(f"# MB7574 Kammertest, Lauf {run.name}, Soll {a.soll:+g} °C, Tag '{a.tag}', Port {port}, "
                  f"{a.zyklen} Zyklen je {a.ein:g} s ein / {a.aus:g} s aus\n"
                  "# zeit\tt_s\tzyklus\tphase\tzeile\n")
        reader = SensorReader(ser, log)
        reader.start()
        print(f"Lauf: {run.name}\nLog:  {log_path.name}\nNetzteil AUS lassen bis zur Ansage.")
        time.sleep(2)
        for k in range(1, a.zyklen + 1):
            reader.cycle, reader.phase = k, "EIN"
            reader.note(k, "EIN", "# Ansage Netzteil EIN")
            print(f"\a\nZyklus {k}/{a.zyklen}: Netzteil EIN" + ("   (Strom ablesen)" if k == 1 else ""))
            countdown(a.ein, "EIN", reader)
            reader.phase = "AUS"
            reader.note(k, "AUS", "# Ansage Netzteil AUS")
            print(f"\aZyklus {k}/{a.zyklen}: Netzteil AUS")
            if k < a.zyklen:
                countdown(a.aus, "AUS", reader)
            else:
                time.sleep(1)       # last cycle: no off-time needed, only catch the final line
        reader.halt.set()
        reader.join()
        current_text = input("\nStrom während EIN in mA (leer = nicht abgelesen): ").strip()
        remark = input("Bemerkung: ").strip()
        reader.note(0, "-", f"# Strom {current_text or '-'} mA; Bemerkung: {remark}")

    values, n_no_echo, invalid, with_data, with_header, per_cycle = evaluate(reader.lines, a.zyklen)
    median = statistics.median(values) if values else None
    median_z1, drift = cycle_stats(per_cycle)
    current = number(current_text) if current_text else None
    row = dict(zip(SUMMARY_COLUMNS, [
        start.isoformat(timespec="seconds"), f"{a.soll:g}", a.tag, a.zyklen, with_data, with_header,
        len(values), n_no_echo, invalid, fmt(median), fmt(min(values) if values else None),
        fmt(max(values) if values else None), "", fmt(current), remark, log_path.name,
        fmt(median_z1), fmt(drift)]))
    ref = update_summary(csv_path, row)
    dev = number(row["abw_ref_pct"]) if row["abw_ref_pct"] else None

    flags = []
    if with_data < a.zyklen:
        flags.append(f"nur {with_data}/{a.zyklen} Zyklen mit Daten")
    if with_header < a.zyklen:
        flags.append(f"nur {with_header}/{a.zyklen} Zyklen mit Kopfzeile")
    if n_no_echo:
        flags.append(f"{n_no_echo} × R5000")
    if invalid:
        flags.append(f"{invalid} ungültige Zeilen")
    if dev is not None and abs(dev) > LIMIT_MEDIAN_PCT:
        flags.append(f"Zyklus 1 {dev:+.1f} % gegen +20 °C")
    if ref and ref["strom_mA"] and current is not None:
        dev_current = 100 * (current / float(ref["strom_mA"]) - 1)
        if abs(dev_current) > LIMIT_CURRENT_PCT:
            flags.append(f"Strom {dev_current:+.0f} % gegen +20 °C")

    print(f"\nSoll {a.soll:+g} °C: Zyklen mit Daten {with_data}/{a.zyklen}, mit Kopfzeile {with_header}/{a.zyklen}")
    print(f"Werte {len(values)} (R5000: {n_no_echo}, ungültig: {invalid})")
    if values:
        print(f"Median alle Werte {median:g} mm (min {min(values)}, max {max(values)})")
    if median_z1 is not None:
        print(f"Median Zyklus 1 (Kaltstart) {median_z1:g} mm"
              + (f", Anstieg bis Zyklus {len(per_cycle)}: {drift:+g} mm" if drift is not None else "")
              + (f", gegen +20 °C {dev:+.1f} %" if dev is not None else ""))
    print("AUFFÄLLIG: " + "; ".join(flags) if flags else "keine Auffälligkeit")


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
    for args in (["add", "--", "."],
                 ["commit", "-m", f"schneehoehensensor: Lauf {run.name}", "--", "."],
                 ["push"]):
        if subprocess.run(["git", "-C", str(run), *args]).returncode:
            hint = " (erst 'git pull --rebase', dann erneut abschliessen)" if args[0] == "push" else ""
            sys.exit(f"git {args[0]} fehlgeschlagen{hint}")
    print("Eingecheckt und gepusht.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--laeufe", type=Path, default=RUNS_DIR, help=argparse.SUPPRESS)   # tests only
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("neu", help="Laufordner anlegen")

    st = sub.add_parser("stufe", help="eine Temperaturstufe messen")
    st.add_argument("soll", type=float, help="Solltemperatur der Kammer in °C")
    st.add_argument("--tag", default="", help="Zusatz im Dateinamen, z. B. tisch, 60min, wdh, ende")
    st.add_argument("--zyklen", type=int, default=3)
    st.add_argument("--ein", type=float, default=20, help="Sekunden Netzteil ein (Standard 20)")
    st.add_argument("--aus", type=float, default=10, help="Sekunden Netzteil aus (Standard 10)")
    st.add_argument("--port", help="serieller Port, Standard: einziger /dev/cu.usbserial*")
    st.add_argument("--lauf", help="Laufordner (Name oder Pfad), Standard: der neueste")

    ab = sub.add_parser("abschliessen", help="Notizen anlegen, Laufordner einchecken und pushen")
    ab.add_argument("--lauf", help="Laufordner (Name oder Pfad), Standard: der neueste")

    a = ap.parse_args()
    {"neu": cmd_new, "stufe": cmd_step, "abschliessen": cmd_close}[a.command](a)


if __name__ == "__main__":
    main()
