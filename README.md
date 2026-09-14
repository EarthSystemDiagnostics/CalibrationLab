# CalibrationLab

Labor-Repo: Code, Anleitungen und Messläufe der Labortests. Privat.

Vorgehen bei Labortests: **`SOP_Labortests.md`**.

## Vorhaben

| Ordner | Inhalt | Einstieg |
|---|---|---|
| `kalibrierung/` | NTC-Kalibrierung gegen SPRT mit Kalibrierbad: Logger, Badsteuerung, R-Auswertung, Läufe, Archiv bis Juli 2026 | `kalibrierung/docs/HANDOVER.md` |
| `schneehoehensensor/` | Kammertests MaxBotix MB7574 (folgt) | |

## Einrichten

macOS, Python 3: `python3 -m pip install -r requirements.txt` (pyserial, minimalmodbus). Serielle Ports heißen `/dev/cu.usbserial-*`; welcher Adapter wo steckt, zeigt `python3 kalibrierung/tools/port_detect.py --list`.
