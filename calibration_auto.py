#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Automated calibration run: drive the Isotech bath through a list of temperature
plateaus while logging MicroK (SPRT) + SchwaRTech/AWI head (NTCs) continuously.

This is the "with bath control" companion to calibration_log.py (which stays the
passive, manual-setpoint legacy tool). Everything about the two loggers is reused
unchanged -- this script only adds the bath orchestration on top:

    for each plateau:
        (optional) set an approach ramp rate
        command the setpoint
        wait until the bath is stable (PV in tolerance band for a while)
        dwell / measure for the configured number of minutes
        record the plateau's timestamps

The MicroK and NTC loggers run for the whole session, exactly as in the legacy
tool, so the two data streams still match up by wall-clock timestamp. An extra
file  <exp>_<time>_plateaus.txt  records, per plateau, the setpoint and the
timestamps (commanded / stable / measurement start / measurement end) so you can
slice out each plateau's stable window afterwards.

Usage:
    python3 calibration_auto.py                     # uses param_combined.txt
    python3 calibration_auto.py --param x.txt        # different config file
    python3 calibration_auto.py --exp Run7           # override experiment name
    python3 calibration_auto.py --dry-run            # read bath only, don't move it

Stop: Ctrl-C -> loggers shut down cleanly; the bath is left at its last setpoint.

See param_combined.txt for the bath-automation keys and bath.py for the
controller-specific settings (encoding, baud, registers) that must be verified
once against the real Eurotherm controller.
"""

import os
import sys
import time
import argparse
import threading
from datetime import datetime

# Reuse the legacy logging machinery unchanged.
from calibration_log import (
    read_config, pick_port, microk_worker, logger_worker, write_meta,
)
from bath import Bath
import sprt
import ntc


# --------------------------------------------------------------------------
# Bath-automation configuration (parsed from the same parameter file)
# --------------------------------------------------------------------------
def read_bath_config(param_path):
    """Parse the bath-automation keys from the parameter file.

    Same key:value / ';'-separated-list style as the rest of the config.
    List rules for plateau_minutes and ramp_c_per_min:
        one value  -> applied to every plateau
        N values   -> one per plateau (N must equal the number of plateaus)
    """
    cfg = {}
    with open(param_path, "r") as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, val = line.split(":", 1)
            val = val.split("#", 1)[0]          # drop inline comments after the value
            cfg[key.strip().lower()] = val.strip()

    def as_list(key):
        return [x.strip() for x in cfg.get(key, "").split(";") if x.strip()]

    b = {}
    b["port_hint"] = cfg.get("bath_port", "")
    b["slave"]     = int(cfg.get("bath_slave", "1"))

    enc = cfg.get("bath_encoding", "int1").lower()
    if enc == "float":
        b["use_float"], b["decimals"] = True, 1
    elif enc in ("int0", "int1", "int2", "int3"):
        b["use_float"], b["decimals"] = False, int(enc[-1])
    else:
        sys.exit(f"bath_encoding must be one of float|int0|int1|int2|int3 (got {enc!r})")

    plateaus = [float(x) for x in as_list("plateaus")]
    if not (1 <= len(plateaus) <= 20):
        sys.exit("plateaus: give between 1 and 20 temperature values (deg C)")
    b["plateaus"] = plateaus
    n = len(plateaus)

    def expand(key, values, what):
        if len(values) == 1:
            return values * n
        if len(values) == n:
            return values
        sys.exit(f"{key}: give one {what} (applies to all) or exactly {n} (one per plateau)")

    mins = [float(x) for x in as_list("plateau_minutes")]
    if not mins:
        sys.exit("plateau_minutes: give the dwell time in minutes (one value, or one per plateau)")
    b["minutes"] = expand("plateau_minutes", mins, "dwell time")

    ramp_raw = as_list("ramp_c_per_min")
    if not ramp_raw:
        b["ramps"] = [None] * n            # empty -> max speed for all plateaus
    else:
        rv = expand("ramp_c_per_min", [float(x) for x in ramp_raw], "ramp rate")
        b["ramps"] = [None if r == 0 else r for r in rv]   # 0 == rate limit off

    b["tol"]        = float(cfg.get("stability_tol", "0.02"))
    b["window_min"] = float(cfg.get("stability_window", "10"))
    # Stability timeout scales with the step size: `per_10k` minutes are allowed
    # for every 10 K the bath has to travel, with a floor for small/zero steps.
    # If the bath is still not stable when the timeout elapses, we measure anyway.
    b["timeout_per_10k"] = float(cfg.get("stability_timeout_per_10k", "30"))
    b["timeout_floor"]   = float(cfg.get("stability_timeout_min", "15"))
    return b


def plateau_timeout_min(b, delta_k):
    """Timeout in minutes for a step of |delta_k| Kelvin: per_10k per 10 K,
    but never below the floor (so tiny steps still get a fair chance)."""
    return max(b["timeout_floor"], b["timeout_per_10k"] * abs(delta_k) / 10.0)


# --------------------------------------------------------------------------
# Plateau schedule file
# --------------------------------------------------------------------------
def open_plateaus_file(path, b):
    fh = open(path, "w")
    fh.write("# Plateau schedule for automated calibration run\n")
    fh.write(f"# stability: tol={b['tol']} C, window={b['window_min']} min, "
             f"timeout={b['timeout_per_10k']} min/10K (floor {b['timeout_floor']} min)\n")
    fh.write("# columns: idx; setpoint_C; ramp_C_per_min; t_command; t_stable; "
             "t_dwell_start; t_dwell_end; stable_ok\n")
    fh.flush()
    return fh


def latest_sprt_temp(microk_file):
    """Read the last MicroK line from the log file and convert it to deg C.

    Decoupled from the logger thread on purpose: the worker keeps writing +
    flushing each reading, so we just tail the file. Returns (temp_C, channel)
    or None if nothing usable is there yet. MicroK line layout (see
    calibration_log.py): t_min; datetime; ratio; current; ChannelN; index
    """
    try:
        with open(microk_file, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 4096))     # last few lines are plenty
            tail = f.read().decode("utf-8", "replace")
        for line in reversed(tail.splitlines()):
            parts = [p.strip() for p in line.split(";")]
            if len(parts) >= 5:
                ratio = float(parts[2])
                channel = parts[4]
                return sprt.ratio_to_temp_c(ratio, channel), channel
    except Exception:
        pass
    return None


def sprt_status(microk_file):
    """One-line 'SPRT=... C' string for status displays (empty if no reading)."""
    res = latest_sprt_temp(microk_file)
    if res is None:
        return "SPRT=--.-- C"
    temp, channel = res
    return f"SPRT={temp:+.4f} C ({channel})"


def latest_ntc1_temps(ntc_file):
    """Tail the NTC log and return [(node_label, raw_temp_C), ...] for NTC1 of
    each node in the most recent measurement row, or None.

    Robust by design: the NTC1 columns are located from the repeated HEADER line
    (which carries the `Nxx_NTC1` labels), so standalone columns and channel order
    are handled without assumptions. Rows are split as `Group; secs; datetime;
    datablock` -- a data row has a numeric 2nd field and no `NTC`/`New Node Array`
    text in its datablock (that filters out headers, echoes and meta lines).
    """
    try:
        with open(ntc_file, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 8192))
            lines = f.read().decode("utf-8", "replace").splitlines()

        data_idx = None
        for i in range(len(lines) - 1, -1, -1):
            parts = lines[i].split(";", 3)
            if len(parts) != 4 or not parts[0].strip().startswith("Group"):
                continue
            try:
                float(parts[1])
            except ValueError:
                continue                              # header row (secs = text)
            block = parts[3]
            if block.strip() and "NTC" not in block and "New Node Array" not in block:
                data_idx = i
                break
        if data_idx is None:
            return None

        dparts = lines[data_idx].split(";", 3)
        dgroup, dblock = dparts[0].strip(), dparts[3]
        header = None
        for i in range(data_idx, -1, -1):            # nearest preceding header, same group
            hp = lines[i].split(";", 3)
            if len(hp) == 4 and hp[0].strip() == dgroup and "NTC1" in hp[3]:
                header = hp[3]
                break
        if header is None:
            return None
        return ntc.ntc1_from_row(header, dblock) or None
    except Exception:
        return None


def ntc1_status(ntc_file):
    """Compact 'NTC1[C] N90=+.. N91=+..' string for status displays."""
    res = latest_ntc1_temps(ntc_file)
    if not res:
        return "NTC1=--"
    return "NTC1[C] " + " ".join(f"{node}={t:+.3f}" for node, t in res)


def interruptible_sleep(seconds, label, extra=None):
    """Sleep in small steps so Ctrl-C stays responsive; print a coarse countdown.

    `extra` is an optional callable returning a status string to append.
    """
    end = time.time() + seconds
    while True:
        remaining = end - time.time()
        if remaining <= 0:
            return
        suffix = ""
        if extra is not None:
            try:
                suffix = "   " + extra()
            except Exception:
                suffix = ""
        print(f"  [{label}] {remaining/60:5.1f} min remaining ...{suffix}")
        time.sleep(min(15.0, remaining))


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Automated bath-driven calibration run")
    ap.add_argument("--param", default="param_combined.txt", help="path to the parameter file")
    ap.add_argument("--exp", default=None, help="override the experiment name")
    ap.add_argument("--dry-run", action="store_true",
                    help="connect and read the bath but never change its setpoint")
    args = ap.parse_args()

    c = read_config(args.param, exp_override=args.exp)
    b = read_bath_config(args.param)

    print("Experiment :", c["exp"])
    print("MicroK     : Ref", c["ref_ch"], "| SPRT", c["sprt_chs"], "| hint", c["microk_hint"])
    print("TempHead   : readout", c["active"], "| nodes", c["Logger_sensorNo"], "| hint", c["logger_hint"])
    print("Bath       : slave", b["slave"], "| encoding",
          "float" if b["use_float"] else f"int{b['decimals']}", "| hint", b["port_hint"])
    print("Plateaus   :")
    for i, (sp, mn, rp) in enumerate(zip(b["plateaus"], b["minutes"], b["ramps"]), 1):
        ramp = "max speed" if rp is None else f"{rp} C/min"
        print(f"   {i:2d}. {sp:+7.2f} C   dwell {mn:g} min   ramp {ramp}")

    description = input("\nDescription of this calibration (press Enter to confirm): ").strip()

    # Select ports interactively (same helper as the legacy tool).
    microk_port = pick_port("MicroK bridge (9600 baud)", c["microk_hint"])
    logger_port = pick_port("SchwaRTech/AWI Temperature head (19200 baud)", c["logger_hint"])
    bath_port   = pick_port("Isotech bath controller (Modbus)", b["port_hint"])
    print("\nSelected  MicroK   ->", microk_port)
    print("Selected  TempHead ->", logger_port)
    print("Selected  Bath     ->", bath_port)

    # File names (same convention as calibration_log.py, plus a plateau file).
    os.makedirs("Output", exist_ok=True)
    run_stamp    = time.strftime("%Y%m%d-%H%M%S")
    microk_file  = f"Output/{c['exp']}_{run_stamp}_microk.txt"
    logger_file  = f"Output/{c['exp']}_{run_stamp}_ntc.txt"
    meta_file    = f"Output/{c['exp']}_{run_stamp}_meta.txt"
    plateau_file = f"Output/{c['exp']}_{run_stamp}_plateaus.txt"

    write_meta(meta_file, args.param, c, microk_port, logger_port, microk_file, logger_file,
               run_stamp, description)
    with open(meta_file, "a") as m:
        m.write("\n--- Bath automation ---\n")
        m.write(f"Bath port        : {bath_port}\n")
        m.write(f"Bath slave       : {b['slave']}\n")
        m.write(f"Bath encoding    : {'float' if b['use_float'] else 'int'+str(b['decimals'])}\n")
        m.write(f"Plateaus (C)     : {b['plateaus']}\n")
        m.write(f"Dwell (min)      : {b['minutes']}\n")
        m.write(f"Ramps (C/min)    : {['max' if r is None else r for r in b['ramps']]}\n")
        m.write(f"Stability        : tol={b['tol']} C, window={b['window_min']} min, "
                f"timeout={b['timeout_per_10k']} min/10K (floor {b['timeout_floor']} min)\n")
        m.write(f"Plateau file     : {plateau_file}\n")
    print("Meta file written:", meta_file)

    # --- Connect to the bath first: this is the smoke test for wiring/baud/
    #     slave/encoding. Abort *before* starting the loggers if it fails. ---
    try:
        bath = Bath(bath_port, slave=b["slave"], use_float=b["use_float"], decimals=b["decimals"])
        print(f"\nBath connected. PV={bath.read_pv():+.4f} C  "
              f"SP={bath.read_setpoint():+.4f} C  OUT={bath.read_output():.1f} %")
    except Exception as e:
        sys.exit(
            f"\nCould not talk to the bath controller: {e}\n"
            "Check: RS232 cable/converter, baud+parity in bath.py, slave address, "
            "and the value encoding (bath_encoding: float vs int1/int2/int3)."
        )

    if args.dry_run:
        print("\n--dry-run: not moving the bath. Exiting.")
        return

    # --- Start the two loggers (continuous, for the whole run). ---
    stop_event = threading.Event()
    t_micro  = threading.Thread(target=microk_worker,
                                args=(stop_event, c, microk_port, microk_file),
                                daemon=True, name="MicroK")
    t_logger = threading.Thread(target=logger_worker,
                                args=(stop_event, c, logger_port, logger_file),
                                daemon=True, name="TempHead")
    t_micro.start()
    t_logger.start()
    print("\nLoggers running. Starting plateau schedule. Stop anytime with Ctrl-C.\n")

    pf = open_plateaus_file(plateau_file, b)
    try:
        for i, (sp, minutes, ramp) in enumerate(zip(b["plateaus"], b["minutes"], b["ramps"]), 1):
            print(f"\n===== Plateau {i}/{len(b['plateaus'])}: setpoint {sp:+.3f} C "
                  f"(dwell {minutes:g} min, ramp {'max' if ramp is None else str(ramp)+' C/min'}) =====")

            # Timeout scales with how far the bath must travel from where it is
            # right now (30 min/10 K by default). If it still hasn't settled by
            # then, we measure anyway rather than block the whole run.
            pv_now  = bath.read_pv()
            timeout = plateau_timeout_min(b, sp - pv_now)
            print(f"  step {pv_now:+.2f} -> {sp:+.2f} C (|dT|={abs(sp-pv_now):.1f} K); "
                  f"stability timeout {timeout:.0f} min.")

            bath.set_ramp_rate(ramp)          # None/0 -> rate limit off (max speed)
            bath.set_setpoint(sp)
            t_command = datetime.now()

            # Live SPRT reading (from the MicroK log) shown next to bath PV/SP.
            status = lambda: sprt_status(microk_file)

            ok = bath.wait_until_stable(
                sp, tol=b["tol"],
                window_s=b["window_min"] * 60,
                poll_s=5.0,
                timeout_s=timeout * 60,
                extra=status,
            )
            t_stable = datetime.now()
            if ok:
                print(f"  -> stable at {sp:+.3f} C. Measuring for {minutes:g} min.")
            else:
                print(f"  !! NOT stable within {timeout:.0f} min. "
                      f"Measuring anyway -- check this plateau afterwards.")

            t_dwell_start = datetime.now()
            # During the dwell, show bath PV/SP, the SPRT temperature, and the raw
            # NTC1 temperature per node -- the full picture at the plateau.
            dwell_status = lambda: (f"bath PV={bath.read_pv():+.3f} SP={sp:+.3f} | "
                                    f"{sprt_status(microk_file)} | {ntc1_status(logger_file)}")
            interruptible_sleep(minutes * 60, f"plateau {i} dwell", extra=dwell_status)
            t_dwell_end = datetime.now()

            pf.write(f"{i}; {sp}; {'off' if ramp is None else ramp}; "
                     f"{t_command}; {t_stable}; {t_dwell_start}; {t_dwell_end}; {ok}\n")
            pf.flush()
            print(f"  Plateau {i} done.")

        print("\nAll plateaus complete. Stopping loggers ...")
    except KeyboardInterrupt:
        print("\nInterrupted. Stopping loggers ...")
    finally:
        stop_event.set()
        t_micro.join(timeout=15)
        t_logger.join(timeout=15)
        pf.close()
        print("Done. Files are in ./Output/  (bath left at its last setpoint).")


if __name__ == "__main__":
    main()
