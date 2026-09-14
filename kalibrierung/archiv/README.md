# Archiv — Kalibrierkampagnen bis Juli 2026

Unveränderte Kopie aus `~/sharedAI/CalibrationChains` vom 14.09.2026; dort weiterhin vorhanden. Nicht umsortieren, nicht korrigieren.

| Ordner | Quelle | Inhalt |
|---|---|---|
| `2026-07_rekalibrierung/` | `recalib2026_07_03/` ohne die Code-Kopie `CalibrationLab/` | Rekalibrierung 03.–07.07.2026, Set 1 (12 Sensoren) und Set 2 (13 Sensoren). `MANIFEST.md` ordnet umbenannte Dateien ihren ursprünglichen Namen zu; `derive_calibration.R`, Ergebnisse in `results/` |
| `2026-07_ntc2test/` | `ntc2test/` | Prüfläufe 01./02.07.2026, Koeffizienten SM1/SM2, `check_deviation.R` |
| `2026-07_recalib2sensors/` | `recalib2sensors/` | Wiederholungstest 5 Sensoren 03.07.2026, `check_repro.R` |

Die R-Skripte setzen absolute Pfade auf den alten Ort (`setwd("/Users/tlaepple/sharedAI/CalibrationChains…")`) und laden `lib/` von dort. Hier laufen sie erst, wenn die Pfade auf `../../auswertung/lib` angepasst sind.

Nicht übernommen: lose Entwicklungs-Logs Mai–Juli 2026 aus der Wurzel von `CalibrationChains` und `test/` mit den Kalibrierungen 2025 (Daten unter `/Users/tlaepple/data/KohnenRecords/data`).
