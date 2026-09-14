# CLAUDE.md — CalibrationLab (Labor-Repo)

Code, Anleitungen und Messläufe der Labortests. Einstieg für Menschen: `README.md`,
Vorgehen bei Labortests: `SOP_Labortests.md`.
Je Vorhaben gilt zusätzlich dessen eigene CLAUDE.md (z. B. `kalibrierung/CLAUDE.md`).

## Aufbau

- Ein Ordner je Vorhaben (`kalibrierung/`, `schneehoehensensor/`, …) mit Code, Tests, Doku und `laeufe/`.
- Gemeinsamer Code erst, wenn ihn zwei Vorhaben brauchen; dann in einen Ordner auf oberster Ebene.

## Regeln für Änderungen

- **Keine Commits oder Pushes ohne ausdrückliches Ja.**
- Laufordner (`*/laeufe/*`) nicht verändern; Rohdaten bleiben, wie sie aufgezeichnet wurden. Ergänzungen nur als `notizen.md`.
- `kalibrierung/archiv/` ist eine unveränderte Kopie aus `~/sharedAI/CalibrationChains` (14.09.2026); nicht umsortieren.
- Dateiformate eines Loggers nur zusammen mit seiner Formatbeschreibung ändern (Kalibrierung: `kalibrierung/docs/DATA_FORMATS.md`).
- Tests bleiben hardwarefrei (Simulator, Fake-Port) und laufen vor jedem Commit.
- Serielle Schreibbefehle bewegen Hardware; erst lesende Wege probieren.
- Code und Code-Doku Englisch, Anleitungen und Laufnotizen Deutsch.
