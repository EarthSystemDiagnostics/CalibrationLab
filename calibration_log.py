#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kombiniertes Kalibrier-Logging (MicroK + Arduino-NTC-Logger) als Command-Line-Tool.

Liest beide seriellen Geraete parallel (je ein Thread) und schreibt pro Lauf drei
Dateien nach ./Output/:  <exp>_<zeit>_microk.txt, _ntc.txt, _meta.txt

Aufruf:
    python calibration_log.py                 # nutzt param_combined.txt
    python calibration_log.py --param x.txt    # andere Konfig
    python calibration_log.py --exp Testlauf   # Experimentname ueberschreiben

Stoppen: Ctrl-C  -> beide Threads beenden sauber und schliessen die Ports.
"""

import os
import time
import argparse
import threading
from datetime import datetime

import serial
import serial.tools.list_ports


# --------------------------------------------------------------------------
# Konfiguration
# --------------------------------------------------------------------------
def read_config(param_path, exp_override=None):
    """Liest die minimale key:value-Parameterdatei und leitet die Konfig ab."""
    cfg = {}
    with open(param_path, "r") as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, val = line.split(":", 1)
            cfg[key.strip().lower()] = val.strip()

    def as_list(key):
        return [x.strip() for x in cfg[key].split(";") if x.strip()]

    c = {}
    c["exp"] = exp_override or cfg["experiment"]

    # --- MicroK ---
    microk_channels = as_list("microk_channels")   # Referenz; SPRT1[; SPRT2]
    c["ref_ch"]      = microk_channels[0]
    c["sprt_chs"]    = microk_channels[1:]
    c["microk_hint"] = cfg.get("microk_port", "")

    # --- Logger ---
    c["logger_hint"]    = cfg.get("ntc_port", "")
    c["active"]         = as_list("ntc_readout")
    _g = as_list("ntc_groups")
    c["Nr_NTCs_group"]  = int(_g[0])
    c["Nr_MeasPoints"]  = int(_g[1])
    c["Logger_sensorNo"] = as_list("ntc_nodes")

    positions = ['DateTime', 'TempADC', 'NTC1', 'NTC2', 'TestSB', 'TestN', 'GND', 'PRESSURE']
    active = c["active"]
    c["on_commands"]  = [f"{p} ON\r\n"  for p in positions if p in active]
    c["off_commands"] = [f"{p} OFF\r\n" for p in positions if p not in active]

    nodes = c["Logger_sensorNo"]
    ng = c["Nr_NTCs_group"]
    groups = [nodes[i:i + ng] for i in range(0, len(nodes), ng)]
    c["commands_groupNTCs"] = [f"NODES {' '.join(g)} \r\n" for g in groups]

    standalones = ["DateTime", "TempADC"]
    standalone_cols = [s for s in standalones if s in active]
    node_positions  = [a for a in active if a not in standalones]
    headers = []
    for group in groups:
        node_cols = [" | ".join(f"N{node.strip()}_{pos}" for pos in node_positions) for node in group]
        headers.append("SecondsElapsed; DateTimePC; " + " || ".join(standalone_cols + node_cols))
    c["headers"] = headers

    return c


# --------------------------------------------------------------------------
# Serielle Ports zur Laufzeit auswaehlen
# --------------------------------------------------------------------------
def pick_port(role, hint):
    """Fragt nach dem Port-Index. Enter = Vorauswahl anhand 'hint' aus der Konfig."""
    ports = list(serial.tools.list_ports.comports())
    print("\nErkannte serielle Ports:")
    for idx, p in enumerate(ports):
        print(f"  [{idx}]  {p.device}   |   {p.description}   |   {p.hwid}")
    default_idx = next((i for i, p in enumerate(ports) if hint and hint in p.device), None)
    prompt = f"Index fuer {role}"
    if default_idx is not None:
        prompt += f"  [Enter = {default_idx}: {ports[default_idx].device}]"
    prompt += ": "
    while True:
        choice = input(prompt).strip()
        if choice == "" and default_idx is not None:
            return ports[default_idx].device
        if choice.isdigit() and int(choice) < len(ports):
            return ports[int(choice)].device
        print("  Ungueltig - bitte eine der Indexnummern oben eingeben.")


# --------------------------------------------------------------------------
# Worker: je ein Geraet, eigener Serial-Port, eigene Datei
# --------------------------------------------------------------------------
def microk_worker(stop_event, c, microk_port, microk_file):
    try:
        ser = serial.Serial(microk_port, 9600, serial.EIGHTBITS,
                             serial.PARITY_NONE, serial.STOPBITS_ONE, timeout=2)
    except Exception as e:
        print("[MicroK] Port-FEHLER:", e)
        return
    print("[MicroK] Port offen :", microk_port)
    queries = [(ch, f"MEAS:RAT{ch}:REF{c['ref_ch']}? 100,0.56 \r\n") for ch in c["sprt_chs"]]
    if not queries:
        print("[MicroK] Keine SPRT-Kanaele konfiguriert -> nichts zu messen.")
        ser.close()
        return
    start = time.time()
    n = 0
    print("[MicroK] Datei      :", microk_file)
    print("[MicroK] Warte auf Daten ...")
    try:
        with open(microk_file, "a") as f:
            while not stop_event.is_set():
                for ch, cmd in queries:
                    ser.write(cmd.encode("ascii"))
                    data = ""
                    while not data and not stop_event.is_set():
                        data = ser.readline().decode("utf-8", "replace").strip()
                    if not data:
                        continue
                    n += 1
                    t_min = (time.time() - start) / 60
                    f.write(f"{t_min};{datetime.now()};{data};0.56mA;Channel{ch};{n}\n")
                    f.flush()
                    print(f"[MicroK] #{n} Ch{ch}: {data}")
    finally:
        ser.close()
        print("[MicroK] gestoppt, Port geschlossen.")


def logger_worker(stop_event, c, logger_port, logger_file):
    try:
        ser = serial.Serial(logger_port, 19200, serial.EIGHTBITS,
                             serial.PARITY_NONE, serial.STOPBITS_ONE, timeout=1)
    except Exception as e:
        print("[TempLogger] Port-FEHLER:", e)
        return
    print("[TempLogger] Port offen :", logger_port)
    Nr_MeasPoints = c["Nr_MeasPoints"]
    headers = c["headers"]
    commands_groupNTCs = c["commands_groupNTCs"]
    try:
        # --- Arduino/Kopf aufwecken und konfigurieren ---
        print("[TempLogger] Wecke Kopf, warte auf Antwort ...")
        ser.write("help\r\n".encode("ascii"))
        received = ""
        while not any(ch.isalpha() for ch in received) and not stop_event.is_set():
            received = ser.readline().decode("utf-8", "replace")
        print("[TempLogger] Kopf ist wach.")
        time.sleep(3)
        ser.write("LIVE \r\n".encode("ascii")); time.sleep(3)
        ser.write("nodes on \r\n".encode("ascii")); time.sleep(2); time.sleep(3)
        for command in c["on_commands"] + c["off_commands"]:
            print("[TempLogger] sende:", command.strip())
            ser.write(command.encode("ascii")); time.sleep(5)

        start = datetime.now()
        print("[TempLogger] Datei      :", logger_file)
        print("[TempLogger] Warte auf Messdaten ...")
        with open(logger_file, "a") as fh:
            while not stop_event.is_set():
                for g_idx in range(len(headers)):
                    ser.write(commands_groupNTCs[g_idx].encode("ascii"))
                    r = False
                    i = 0
                    while i < (Nr_MeasPoints + 3) and not stop_event.is_set():
                        received = ""
                        while not received and not stop_event.is_set():
                            received = ser.readline().decode("utf-8", "replace")
                        if not received:
                            continue
                        data_values = received.strip()
                        now = datetime.now()
                        sec = (now - start).total_seconds()
                        if "New Node Array:" in received:      # neue Node-Gruppe bestaetigt
                            r = True
                            fh.write(f"Group{g_idx+1}; {headers[g_idx]}\r\n")
                        if r:
                            fh.write(f"Group{g_idx+1}; {sec}; {now}; {data_values}\n")
                            fh.flush()
                            i += 1
                            print(f"[TempLogger] G{g_idx+1} {i}/{Nr_MeasPoints + 3}: {data_values}")
    finally:
        ser.close()
        print("[TempLogger] gestoppt, Port geschlossen.")


# --------------------------------------------------------------------------
# Meta-Datei
# --------------------------------------------------------------------------
def write_meta(meta_file, param_path, c, microk_port, logger_port, microk_file, logger_file, run_stamp):
    with open(meta_file, "w") as m:
        m.write(f"Experiment       : {c['exp']}\n")
        m.write(f"Start (PC-Zeit)  : {datetime.now()}\n")
        m.write(f"Run-Stempel      : {run_stamp}\n")
        m.write("\n--- Aufgeloeste Einstellungen ---\n")
        m.write(f"MicroK-Port      : {microk_port}\n")
        m.write(f"MicroK-Kanaele   : Referenz={c['ref_ch']}  SPRT={c['sprt_chs']}\n")
        m.write(f"TempLogger-Port  : {logger_port}\n")
        m.write(f"NTC-Readout      : {c['active']}\n")
        m.write(f"Gruppen          : {c['Nr_NTCs_group']} Sensoren/Gruppe, {c['Nr_MeasPoints']} Messpunkte\n")
        m.write(f"Node-IDs         : {c['Logger_sensorNo']}\n")
        m.write(f"MicroK-Datei     : {microk_file}\n")
        m.write(f"NTC-Datei        : {logger_file}\n")
        m.write("\n--- Woertliche Kopie der Parameterdatei ---\n")
        with open(param_path) as p:
            m.write(p.read())


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Kombiniertes MicroK + NTC Kalibrier-Logging")
    ap.add_argument("--param", default="param_combined.txt", help="Pfad zur Parameterdatei")
    ap.add_argument("--exp", default=None, help="Experimentname ueberschreiben")
    args = ap.parse_args()

    c = read_config(args.param, exp_override=args.exp)

    print("Experiment :", c["exp"])
    print("MicroK     : Ref", c["ref_ch"], "| SPRT", c["sprt_chs"], "| Hint", c["microk_hint"])
    print("Logger     : readout", c["active"], "| nodes", c["Logger_sensorNo"], "| Hint", c["logger_hint"])
    print("on-cmds    :", [x.strip() for x in c["on_commands"]])
    print("group-cmds :", [x.strip() for x in c["commands_groupNTCs"]])

    # Ports interaktiv waehlen
    microk_port = pick_port("MicroK-Bridge (9600 Baud)", c["microk_hint"])
    logger_port = pick_port("Arduino-Logger (19200 Baud)", c["logger_hint"])
    print("\nGewaehlt  MicroK ->", microk_port)
    print("Gewaehlt  Logger ->", logger_port)

    # Dateinamen
    os.makedirs("Output", exist_ok=True)
    run_stamp   = time.strftime("%Y%m%d-%H%M%S")
    microk_file = f"Output/{c['exp']}_{run_stamp}_microk.txt"
    logger_file = f"Output/{c['exp']}_{run_stamp}_ntc.txt"
    meta_file   = f"Output/{c['exp']}_{run_stamp}_meta.txt"

    write_meta(meta_file, args.param, c, microk_port, logger_port, microk_file, logger_file, run_stamp)
    print("Meta-Datei geschrieben:", meta_file)

    # Threads starten
    stop_event = threading.Event()
    t_micro  = threading.Thread(target=microk_worker,
                                args=(stop_event, c, microk_port, microk_file),
                                daemon=True, name="MicroK")
    t_logger = threading.Thread(target=logger_worker,
                                args=(stop_event, c, logger_port, logger_file),
                                daemon=True, name="Logger")
    t_micro.start()
    t_logger.start()
    print("\nBeide Logger laufen parallel. Stoppen mit Ctrl-C.")

    try:
        while t_micro.is_alive() or t_logger.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStoppe beide ...")
        stop_event.set()
        t_micro.join(timeout=15)
        t_logger.join(timeout=15)
        print("Fertig. Dateien liegen in ./Output/")


if __name__ == "__main__":
    main()
