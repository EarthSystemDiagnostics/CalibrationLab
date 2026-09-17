# Kammertest MB7574 — Kälteüberleben

Stand 17.09.2026. Alle Beobachtungen mit Uhrzeit ins „Laborbuch Schneehöhensensor 1“. Allgemeines Vorgehen: `SOP_Labortests.md` in der Repo-Wurzel.

## Ziel

Bis zu welcher Kammertemperatur startet der MB7574 und liefert plausible Distanzwerte, und kommt er nach einem Ausfall beim Aufwärmen zurück? Temperatur ist die Kammeranzeige (±0,5 °C); einen eigenen Fühler gibt es nicht.

## Aufbau

- Verdrahtung, Stecklage und Prüfung vor dem Einschalten: `../aufbau/Laboraufbau_MB7574.md`.
- Sensor starr montiert, Strahl rechtwinklig auf eine ebene Fläche in 0,6–1 m Abstand (Kammerwand, Boden oder Platte). Den Aufbau bis Testende nicht verändern.
- Netzteil 5,00 V, Strombegrenzung 150 mA.

## Terminal

Terminal öffnen: Cmd-Leertaste, „Terminal“ tippen, Enter. Befehle eintippen und mit Enter ausführen. Das Terminal arbeitet immer in einem Ordner:

| Befehl | macht |
|---|---|
| `pwd` | zeigt, in welchem Ordner man gerade ist |
| `cd Ordner` | wechselt in den Ordner; `cd ..` geht eine Ebene zurück |
| `ls` | listet, was im Ordner liegt |
| Tab-Taste | ergänzt angefangene Ordner- und Dateinamen |
| Pfeil nach oben | holt den letzten Befehl zurück, zum Ändern und erneut Ausführen |
| Ctrl-C | bricht einen laufenden Befehl ab |

Einmal zu Beginn in den Ordner des Vorhabens wechseln (am Labor-Laptop liegt der Klon unter `~/CalibrationLab`; heißt er anders: `cd ` tippen, Ordner aus dem Finder ins Terminal ziehen, Enter):

```
cd ~/CalibrationLab/schneehoehensensor
pwd
```

`pwd` muss auf `schneehoehensensor` enden. CoolTerm muss geschlossen sein, sonst ist der Port belegt.

## Befehle

```
./kammer neu
./kammer stufe 20
./kammer stufe -40
./kammer stufe -70 --tag 60min
./kammer abschliessen
```

- **`./kammer neu`** einmal je Kammertest, und wieder, wenn Sensor oder Ziel neu montiert wurden. Fragt Personen, Seriennummer (fehlt sie: eigene Kennung, z. B. `MB7574-01`) und Beschreibung ab.
- **`./kammer stufe <Zahl>`** je Temperaturstufe, die Zahl ist die Solltemperatur in °C. Negative Zahlen direkt schreiben. Die Stufe landet im neuesten Laufordner; einen anderen mit `--lauf <Ordnername>`.
- Ablauf einer Stufe: Das Skript piept und sagt „Netzteil EIN“ bzw. „Netzteil AUS“ an: dreimal 20 s ein, dazwischen je 10 s aus. Beim ersten EIN den Strom am Netzteil ablesen. Danach fragt es „Strom während EIN in mA“ (Zahl, Enter) und „Bemerkung“ (Text oder nur Enter) und zeigt die Zusammenfassung: Median aller Werte, Median von Zyklus 1 (Kaltstart nach der Haltezeit) und den Anstieg bis zum letzten Zyklus (Eigenerwärmung; die 10-s-Pausen kühlen den Sensor nicht zurück).
- **`--tag <Wort>`** hängt einen Zusatz an den Dateinamen: `tisch`, `60min`, `wdh`, `ende`. Bezug für den Median von Zyklus 1 und den Strom ist die erste Stufe `./kammer stufe 20` **ohne** Tag; kommt sie erst später, rechnet das Skript die Abweichungen der früheren Stufen nach.
- **`./kammer abschliessen`** zweimal: Der erste Aufruf legt `notizen.md` an. Nach dem Ausfüllen checkt der zweite Aufruf den Laufordner nach Rückfrage (`j`) ein und pusht.
- Eine laufende Stufe bricht Ctrl-C ab; danach mit `--tag wdh` wiederholen. Hilfe: `./kammer stufe --help`.

## Ablauf

Zwischen den Messungen bleibt der Sensor stromlos: im Dauerbetrieb heizt er sich mit 0,34 W über die Kammertemperatur. Jedes Einschalten ist damit ein Kaltstart.

1. `git pull`, dann `./kammer neu`. Den Namen des Laufordners ins Laborbuch.
2. Tischtest vor dem Einbau: `./kammer stufe 20 --tag tisch`.
3. Stufen: +20 °C → −40 → −50 → −60 → −70 → +20 °C. Je Stufe Solltemperatur erreicht, 30 min halten, dann `./kammer stufe <Solltemperatur>`.
4. Läuft bei −70 °C alles, nach weiteren 60 min `./kammer stufe -70 --tag 60min`. Die letzte Stufe `./kammer stufe 20 --tag ende`.

## Ausfall und Auffälligkeiten

Das Skript meldet als auffällig: Zyklen ohne Kopfzeile oder mit weniger als drei Werten, R5000 (kein Echo), ungültige Zeilen, Median von Zyklus 1 mehr als 2 % und Strom mehr als 20 % neben dem Bezug. Selbst ansehen: nur der Minimalwert, springende Werte (min/max).

Steigt der Median von Zyklus 1 unterhalb −40 °C stetig um etwa 0,2 % je K, misst der Sensor weiter, aber seine interne Temperaturkompensation folgt der Kammer nicht mehr. Das als Bemerkung eingeben und weitermachen.

Bei Ausfall (keine oder unbrauchbare Werte):

1. Netzteil ein, Multimeter auf Gleichspannung: rot an Ader 3 (braun), schwarz an Ader 4 (schwarz). Liegt die Spannung deutlich unter 5 V, zuerst Netzteil, Stecker und Kabel prüfen.
2. Rote Messleitung auf Ader 7 (weiß) umstecken. Etwa 1 mV je mm Distanz bedeutet: der Sensor misst, nur die serielle Ausgabe fällt aus.
3. Nach 15 min die Stufe mit `--tag wdh` wiederholen.
4. Bleibt er aus: nicht weiter abkühlen, sondern 10 K wärmer, 30 min halten, Stufe messen. Läuft er dort wieder, liegt die Grenze dazwischen; optional 5 K kälter nachmessen.
5. Danach auf +20 °C.

## Abschluss

1. Kammer öffnen, Sensor und Ziel nicht bewegen: Abstand Sensor-Stirnfläche bis Zielfläche mit dem Maßband auf 5 mm messen, ins Laborbuch, Foto. Erst am Ende messen, damit der Aufbau während der Stufen unberührt bleibt.
2. Wandler, Verguss und Kabel ansehen (Wasser, Risse), Fotos.
3. `./kammer abschliessen`, `notizen.md` ausfüllen, Fotos nach `fotos/` und abfotografierte Laborbuchseiten nach `scans/` im Laufordner, erneut `./kammer abschliessen`.
