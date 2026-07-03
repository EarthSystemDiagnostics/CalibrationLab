#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Combined calibration logging (MicroK + SchwaRTech/AWI Temperature head) as a command-line tool.

Reads both serial instruments in parallel (one thread each) and writes three files
per run to ./Output/:  <exp>_<time>_microk.txt, _ntc.txt, _meta.txt

Usage:
    python3 calibration_log.py                 # uses param_combined.txt
    python3 calibration_log.py --param x.txt    # different config file
    python3 calibration_log.py --exp Testrun    # override the experiment name

Stop: Ctrl-C  -> both threads shut down cleanly and close their ports.
"""

import os
import time
import argparse
import threading
from datetime import datetime

import serial
import serial.tools.list_ports

import sprt   # SPRT ratio -> temperature (on-screen display only)
import ntc    # NTC raw counts -> temperature (on-screen display only)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
def read_config(param_path, exp_override=None):
    """Read the minimal key:value parameter file and derive the configuration."""
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
    microk_channels = as_list("microk_channels")   # reference; SPRT1[; SPRT2]
    c["ref_ch"]      = microk_channels[0]
    c["sprt_chs"]    = microk_channels[1:]
    c["microk_hint"] = cfg.get("microk_port", "")

    # --- Temperature head ---
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
# Select serial ports at runtime
# --------------------------------------------------------------------------
def pick_port(role, hint):
    """Ask for the port index. Enter = pre-selection based on 'hint' from the config."""
    ports = list(serial.tools.list_ports.comports())
    print("\nDetected serial ports:")
    for idx, p in enumerate(ports):
        print(f"  [{idx}]  {p.device}   |   {p.description}   |   {p.hwid}")
    default_idx = next((i for i, p in enumerate(ports) if hint and hint in p.device), None)
    prompt = f"Index for {role}"
    if default_idx is not None:
        prompt += f"  [Enter = {default_idx}: {ports[default_idx].device}]"
    prompt += ": "
    while True:
        choice = input(prompt).strip()
        if choice == "" and default_idx is not None:
            return ports[default_idx].device
        if choice.isdigit() and int(choice) < len(ports):
            return ports[int(choice)].device
        print("  Invalid - please enter one of the index numbers above.")


# --------------------------------------------------------------------------
# Workers: one device each, own serial port, own file
# --------------------------------------------------------------------------
def microk_worker(stop_event, c, microk_port, microk_file):
    try:
        ser = serial.Serial(microk_port, 9600, serial.EIGHTBITS,
                             serial.PARITY_NONE, serial.STOPBITS_ONE, timeout=2)
    except Exception as e:
        print("[MicroK] Port ERROR:", e)
        return
    print("[MicroK] Port open :", microk_port)
    queries = [(ch, f"MEAS:RAT{ch}:REF{c['ref_ch']}? 100,0.56 \r\n") for ch in c["sprt_chs"]]
    if not queries:
        print("[MicroK] No SPRT channels configured -> nothing to measure.")
        ser.close()
        return
    start = time.time()
    n = 0
    print("[MicroK] File      :", microk_file)
    print("[MicroK] Waiting for data ...")
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
                    # On-screen only: also show the converted SPRT temperature.
                    try:
                        t_c = sprt.ratio_to_temp_c(float(data), f"Channel{ch}")
                        tstr = f"  ->  {t_c:+.4f} C"
                    except ValueError:
                        tstr = ""
                    print(f"[MicroK] #{n} Ch{ch}: {data}{tstr}")
    finally:
        ser.close()
        print("[MicroK] stopped, port closed.")


def logger_worker(stop_event, c, logger_port, logger_file):
    try:
        ser = serial.Serial(logger_port, 19200, serial.EIGHTBITS,
                             serial.PARITY_NONE, serial.STOPBITS_ONE, timeout=1)
    except Exception as e:
        print("[TempHead] Port ERROR:", e)
        return
    print("[TempHead] Port open :", logger_port)
    Nr_MeasPoints = c["Nr_MeasPoints"]
    headers = c["headers"]
    commands_groupNTCs = c["commands_groupNTCs"]
    # Column labels per group (drop the "SecondsElapsed; DateTimePC; " prefix) so
    # we can show the raw NTC1 temperature per node on screen (display only).
    ntc_header_cols = [h.split(";", 2)[2] for h in headers]
    try:
        # --- wake up and configure the temperature head ---
        print("[TempHead] Waking head, waiting for response ...")
        ser.write("help\r\n".encode("ascii"))
        received = ""
        while not any(ch.isalpha() for ch in received) and not stop_event.is_set():
            received = ser.readline().decode("utf-8", "replace")
        print("[TempHead] Head is awake.")
        time.sleep(3)
        ser.write("LIVE \r\n".encode("ascii")); time.sleep(3)
        ser.write("nodes on \r\n".encode("ascii")); time.sleep(2); time.sleep(3)
        for command in c["on_commands"] + c["off_commands"]:
            print("[TempHead] sending:", command.strip())
            ser.write(command.encode("ascii")); time.sleep(5)

        start = datetime.now()
        print("[TempHead] File      :", logger_file)
        print("[TempHead] Waiting for measurement data ...")
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
                        if "New Node Array:" in received:      # new node group confirmed
                            r = True
                            fh.write(f"Group{g_idx+1}; {headers[g_idx]}\r\n")
                        if r:
                            fh.write(f"Group{g_idx+1}; {sec}; {now}; {data_values}\n")
                            fh.flush()
                            i += 1
                            # On-screen only: raw NTC1 temperature per node.
                            temps = ntc.ntc1_from_row(ntc_header_cols[g_idx], data_values)
                            tstr = ("  ->  " + " ".join(f"{node}={t:+.3f}" for node, t in temps)
                                    if temps else "")
                            print(f"[TempHead] G{g_idx+1} {i}/{Nr_MeasPoints + 3}: {data_values}{tstr}")
    finally:
        ser.close()
        print("[TempHead] stopped, port closed.")


# --------------------------------------------------------------------------
# Meta file
# --------------------------------------------------------------------------
def write_meta(meta_file, param_path, c, microk_port, logger_port, microk_file, logger_file, run_stamp, description):
    with open(meta_file, "w") as m:
        m.write(f"Experiment       : {c['exp']}\n")
        m.write(f"Description      : {description}\n")
        m.write(f"Start (PC time)  : {datetime.now()}\n")
        m.write(f"Run stamp        : {run_stamp}\n")
        m.write("\n--- Resolved settings ---\n")
        m.write(f"MicroK port      : {microk_port}\n")
        m.write(f"MicroK channels  : Reference={c['ref_ch']}  SPRT={c['sprt_chs']}\n")
        m.write(f"TempHead port    : {logger_port}\n")
        m.write(f"NTC readout      : {c['active']}\n")
        m.write(f"Groups           : {c['Nr_NTCs_group']} sensors/group, {c['Nr_MeasPoints']} measurement points\n")
        m.write(f"Node IDs         : {c['Logger_sensorNo']}\n")
        m.write(f"MicroK file      : {microk_file}\n")
        m.write(f"NTC file         : {logger_file}\n")
        m.write("\n--- Verbatim copy of the parameter file ---\n")
        with open(param_path) as p:
            m.write(p.read())


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Combined MicroK + NTC calibration logging")
    ap.add_argument("--param", default="param_combined.txt", help="path to the parameter file")
    ap.add_argument("--exp", default=None, help="override the experiment name")
    args = ap.parse_args()

    c = read_config(args.param, exp_override=args.exp)

    print("Experiment :", c["exp"])
    print("MicroK     : Ref", c["ref_ch"], "| SPRT", c["sprt_chs"], "| hint", c["microk_hint"])
    print("TempHead   : readout", c["active"], "| nodes", c["Logger_sensorNo"], "| hint", c["logger_hint"])
    print("on-cmds    :", [x.strip() for x in c["on_commands"]])
    print("group-cmds :", [x.strip() for x in c["commands_groupNTCs"]])

    # Free-text description of the calibration (until Enter) -> goes into the meta file
    description = input("\nDescription of this calibration (press Enter to confirm): ").strip()

    # Select ports interactively
    microk_port = pick_port("MicroK bridge (9600 baud)", c["microk_hint"])
    logger_port = pick_port("SchwaRTech/AWI Temperature head (19200 baud)", c["logger_hint"])
    print("\nSelected  MicroK   ->", microk_port)
    print("Selected  TempHead ->", logger_port)

    # File names
    os.makedirs("Output", exist_ok=True)
    run_stamp   = time.strftime("%Y%m%d-%H%M%S")
    microk_file = f"Output/{c['exp']}_{run_stamp}_microk.txt"
    logger_file = f"Output/{c['exp']}_{run_stamp}_ntc.txt"
    meta_file   = f"Output/{c['exp']}_{run_stamp}_meta.txt"

    write_meta(meta_file, args.param, c, microk_port, logger_port, microk_file, logger_file, run_stamp, description)
    print("Meta file written:", meta_file)

    # Start threads
    stop_event = threading.Event()
    t_micro  = threading.Thread(target=microk_worker,
                                args=(stop_event, c, microk_port, microk_file),
                                daemon=True, name="MicroK")
    t_logger = threading.Thread(target=logger_worker,
                                args=(stop_event, c, logger_port, logger_file),
                                daemon=True, name="TempHead")
    t_micro.start()
    t_logger.start()
    print("\nBoth loggers running in parallel. Stop with Ctrl-C.")

    try:
        while t_micro.is_alive() or t_logger.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping both ...")
        stop_event.set()
        t_micro.join(timeout=15)
        t_logger.join(timeout=15)
        print("Done. Files are in ./Output/")


if __name__ == "__main__":
    main()
