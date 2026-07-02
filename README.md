# SPRT–NTC Calibration Logging

Parallel data logging for thermistor (NTC) calibration against an SPRT reference.
Two serial instruments are read **simultaneously** from a single Jupyter notebook:

- **Isotech MicroK** precision resistance bridge — reads the **SPRT** (reference thermometer).
- **Arduino-based sensor head** — reads the **NTCs** (and optional test channels).

Both streams are timestamped (`datetime`) on every line, so the two data files
can be matched up afterwards purely by their wall-clock timestamps.

> The calibration bath (Isotech/Fluke) is **not** driven yet — set its temperature
> setpoints manually. Automating the bath is a possible next step.

---

## Requirements

- **macOS** (serial ports are addressed as `/dev/cu.*`)
- **Python 3**
- **[pyserial](https://pyserial.readthedocs.io/)** — `pip install pyserial`
- **Jupyter** (or any environment that can run `.ipynb`, e.g. JupyterLab / VS Code / Spyder)

```bash
pip install -r requirements.txt
```

## Hardware / wiring

| Instrument            | Typical USB adapter      | Baud  |
|-----------------------|--------------------------|-------|
| MicroK bridge (SPRT)  | FTDI FT232R (single)     | 9600  |
| Arduino sensor head   | FTDI FT2232H (dual, ch0) | 19200 |

The exact `/dev/cu.usbserial-*` names can change between sessions — the notebook
lets you **pick the port interactively at runtime** (see below).

---

## Usage

1. Edit **`param_combined.txt`** (see format below).
2. Open **`Calibration_combined.ipynb`** and run the cells top to bottom:
   - **Cell 3** reads `param_combined.txt`.
   - **Cell 4** lists the detected serial ports and asks you to pick which one is
     the MicroK and which is the Arduino logger. Press **Enter** to accept the
     pre-selection taken from `param_combined.txt`, or type the index number.
   - **Cell 6** starts both logging threads.
3. Watch the live output — you should see, e.g.:
   ```
   [MicroK] Port offen : /dev/cu.usbserial-A922BJHF
   [TempLogger] Kopf ist wach.
   [MicroK] #1 Ch2: <value>
   [TempLogger] G1 1/63: <values>
   ...
   ```
4. **Stop** by interrupting the kernel (Interrupt / Stop button). Both threads
   shut down cleanly and close their serial ports.

Output files can be **copied at any time while logging** — every line is
`flush()`ed to disk immediately, so a copy always contains all completed lines.

---

## Configuration — `param_combined.txt`

Minimal `key: value` format (order does not matter, `#` starts a comment):

```
experiment: Snowmelt_Retest5Sensors     # goes into the output file names

microk_channels: 1;2                     # Reference ; SPRT1 [ ; SPRT2 ]
microk_port: usbserial-A922BJHF          # optional port pre-selection

ntc_readout: NTC1;NTC2;TestSB            # from: TempADC, NTC1, NTC2, TestSB, TestN, GND, PRESSURE
ntc_groups: 5;60                         # sensors per group ; measurement points per group
ntc_nodes: 90;91;92;93;94                # sensor node IDs
ntc_port: usbserial-FT3GCNKB0            # optional port pre-selection
```

| Key               | Meaning                                                        |
|-------------------|----------------------------------------------------------------|
| `experiment`      | Experiment name, used in output file names                     |
| `microk_channels` | Bridge channels: `Reference; SPRT1` (add `;SPRT2` for two SPRTs) |
| `microk_port`     | Optional MicroK port hint (pre-selection only)                 |
| `ntc_readout`     | Which sensor channels to log                                   |
| `ntc_groups`      | `sensors_per_group ; measurement_points_per_group`             |
| `ntc_nodes`       | Sensor node IDs                                                |
| `ntc_port`        | Optional Arduino port hint (pre-selection only)                |

---

## Output — three files per run, in `./Output/`

```
<experiment>_<YYYYMMDD-HHMMSS>_microk.txt   # MicroK / SPRT data
<experiment>_<YYYYMMDD-HHMMSS>_ntc.txt      # NTC logger data
<experiment>_<YYYYMMDD-HHMMSS>_meta.txt     # resolved settings + verbatim copy of the param file
```

All three share the same timestamp, so one run = one matching set. The `_meta.txt`
records the actually-chosen ports and all parameters, so the measurement settings
are always recoverable later.

---

## Repository contents

| File                         | Purpose                                   |
|------------------------------|-------------------------------------------|
| `Calibration_combined.ipynb` | **Main** notebook — logs both instruments |
| `param_combined.txt`         | Configuration for the combined notebook   |
| `requirements.txt`           | Python dependencies                       |
