# SOP Labortests

Stand 14.09.2026. Gilt für alle Vorhaben in diesem Repo.

## 1. Grundsätze

- Ein Lauf ist ein Ordner `<vorhaben>/laeufe/<experiment>_<JJJJMMTT-HHMMSS>/`. Alle Dateien darin tragen diesen Namensstamm.
- Drei Ebenen der Dokumentation: Der **Logger** schreibt mit, was wann passiert. Das **Laborbuch** hält fest, was gemacht und gesehen wurde. **`notizen.md`** im Laufordner fasst zusammen, was zum Verstehen der Daten nötig ist.
- Rohdaten bleiben, wie sie aufgezeichnet wurden. Korrekturen und Nachträge gehen in `notizen.md`.
- Ein Lauf ist abgeschlossen, wenn `notizen.md` ausgefüllt und sein Ordner gepusht ist. Logger dürfen den Laufordner schon während des Laufs selbst pushen.

## 2. Laborbuch

- Ein gebundenes Buch je Vorhaben, es bleibt am Aufbau: „Laborbuch <Vorhaben> 1“, wenn voll Nr. 2. Verweise in der Form `LB Schneehöhensensor 1, S. 12`.
- Seiten nummeriert, jeder Tag mit Datum und Personen, keine Seiten herausreißen, Fehler lesbar durchstreichen.
- Hinein gehören Handgriffe, Skizzen, Beobachtungen, Umbauten und Ideen, auch an Tagen ohne Lauf. Bei einem Lauf der Name des Laufordners.
- Arbeiten ohne Lauf (Aufbau, Löten, Reparatur) stehen nur im Laborbuch. Ändert sich der Aufbau dauerhaft, wird zusätzlich die Aufbaudoku angepasst.
- Volle Bücher zehn Jahre aufbewahren.

## 3. Vor dem Lauf

1. Am Labor-Laptop `git pull`.
2. Anleitung (`<vorhaben>/anleitungen/`) und Aufbaudoku (`<vorhaben>/aufbau/`) lesen. Weicht der Aufbau ab: Anleitung vorher ändern oder die Abweichung im Laborbuch und in `notizen.md` festhalten.
3. Prüfling mit Typ und Seriennummer bereitlegen.
4. Aufbau mit der Checkliste der Aufbaudoku prüfen, ein Foto machen.
5. Ist ein Tischtest vorgesehen, ihn vor dem eigentlichen Lauf fahren.

## 4. Während des Laufs

1. Logger des Vorhabens starten. Er legt den Laufordner an und fragt Personen, Seriennummer und Beschreibung ab. Den Namen des Laufordners ins Laborbuch schreiben.
2. Handgriffe nach Ansage ausführen.
3. Beobachtungen und Unerwartetes mit Uhrzeit ins Laborbuch.
4. Ein Programm pro seriellem Port. Bei langen Läufen den Ruhezustand verhindern: Logger mit `caffeinate -i python3 …` starten.

## 5. Nach dem Lauf

1. `notizen.md` im Laufordner: Abweichungen von der Anleitung, Auffälligkeiten, Ergebnis in einem Satz, Verweis auf die Laborbuchseiten.
2. Laborbuchseiten des Laufs abfotografieren und nach `scans/` legen, Fotos des Aufbaus nach `fotos/`. Höchstens 1600 px, kein HEIC.
3. Einchecken: `git add <vorhaben>/laeufe/<lauf>`, `git commit -m "<vorhaben>: Lauf <lauf>"`, `git push`.
4. Ergebnis-Einzeiler in die Läufe-Liste von `<vorhaben>/README.md`.
5. Nach einer abgeschlossenen Testkampagne: Confluence-Seite mit Ergebnis, Entscheidung und Links auf die Laufordner.

## 6. Anleitungen und Code ändern

- Anleitungen sind Markdown. Word oder PDF zum Drucken bei Bedarf erzeugen, nicht einchecken.
- Code nur mit durchlaufenden Tests einchecken. Tests brauchen keine Hardware (Simulator, Fake-Port).
- Ein Dateiformat nur zusammen mit seiner Formatbeschreibung ändern.
- Ein neuer Logger legt den Laufordner an, schreibt `…_meta.txt` mit Beschreibung, Parametern und Code-Commit, und protokolliert Ansagen und Bemerkungen mit Zeitstempel.

## 7. Neues Vorhaben

Ordner `<vorhaben>/` mit `README.md` (Fragestellung, Stand, Läufe), `aufbau/`, `anleitungen/`, `skripte/`, `tests/` und `laeufe/`; eine Zeile in der Vorhaben-Tabelle der Wurzel-`README.md`; ein gebundenes Laborbuch „<Vorhaben> 1“.

## 8. Zugang

- **Labor-Laptop:** Push über einen Deploy-Key mit Schreibrecht nur für dieses Repo, Git-Kennung „Labor-Laptop“. Wer gemessen hat, steht in `…_meta.txt`. (Deploy-Key noch einzurichten.)
- **Eigene Rechner:** persönlicher GitHub-Zugang zur Organisation `EarthSystemDiagnostics`.
