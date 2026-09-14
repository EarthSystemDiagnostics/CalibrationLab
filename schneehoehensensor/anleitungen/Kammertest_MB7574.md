# Kammertest MB7574 — Kälteüberleben

Stand 14.09.2026. Alle Beobachtungen mit Uhrzeit ins „Laborbuch Schneehöhensensor 1“. Allgemeines Vorgehen: `SOP_Labortests.md` in der Repo-Wurzel.

## Ziel

Bis zu welcher Kammertemperatur startet der MB7574 und liefert plausible Distanzwerte, und kommt er nach einem Ausfall beim Aufwärmen zurück? Temperatur ist die Kammeranzeige (±0,5 °C); einen eigenen Fühler gibt es nicht.

## Aufbau

- Verdrahtung, Stecklage und Prüfung vor dem Einschalten: `../aufbau/Laboraufbau_MB7574.md`.
- Sensor starr montiert, Strahl rechtwinklig auf eine ebene Fläche in 0,6–1 m Abstand (Kammerwand, Boden oder Platte). Den Aufbau bis Testende nicht verändern.
- Abstand Sensor-Stirnfläche bis Zielfläche mit dem Maßband auf 1 mm messen, ins Laborbuch.
- Netzteil 5,00 V, Strombegrenzung 150 mA.

## Befehle

Terminal öffnen und in den Repo-Ordner wechseln (am Labor-Laptop der Ordner des Klons, z. B. `cd ~/CalibrationLab`). CoolTerm muss geschlossen sein, sonst ist der Port belegt.

```
python3 schneehoehensensor/skripte/mb7574_kammerlog.py neu
python3 schneehoehensensor/skripte/mb7574_kammerlog.py stufe 20
python3 schneehoehensensor/skripte/mb7574_kammerlog.py stufe -40
python3 schneehoehensensor/skripte/mb7574_kammerlog.py stufe -70 --tag 60min
python3 schneehoehensensor/skripte/mb7574_kammerlog.py abschliessen
```

- **`neu`** einmal je Kammertest, und wieder, wenn Sensor oder Ziel neu montiert wurden. Fragt Personen, Seriennummer (fehlt sie: eigene Kennung auf dem Gehäuse, z. B. `MB7574-01`) und Beschreibung ab.
- **`stufe <Zahl>`** je Temperaturstufe, die Zahl ist die Solltemperatur in °C. Negative Zahlen direkt schreiben (`stufe -40`). Die Stufe landet im neuesten Laufordner; einen anderen mit `--lauf <Ordnername>`.
- Ablauf einer Stufe: Das Skript piept und sagt „Netzteil EIN“ bzw. „Netzteil AUS“ an: dreimal 20 s ein, dazwischen je 10 s aus. Beim ersten EIN den Strom am Netzteil ablesen. Danach fragt es „Strom während EIN in mA“ (Zahl, Enter) und „Bemerkung“ (Text oder nur Enter) und zeigt die Zusammenfassung: Median aller Werte, Median von Zyklus 1 (Kaltstart nach der Haltezeit) und den Anstieg bis zum letzten Zyklus (Eigenerwärmung; die 10-s-Pausen kühlen den Sensor nicht zurück).
- **`--tag <Wort>`** hängt einen Zusatz an den Dateinamen: `tisch`, `60min`, `wdh`, `ende`. Bezug für den Median von Zyklus 1 und den Strom ist die erste Stufe `stufe 20` **ohne** Tag; kommt sie erst später, rechnet das Skript die Abweichungen der früheren Stufen nach.
- **`abschliessen`** zweimal: Der erste Aufruf legt `notizen.md` an. Nach dem Ausfüllen checkt der zweite Aufruf den Laufordner nach Rückfrage (`j`) ein und pusht.
- Eine laufende Stufe bricht Ctrl-C ab; danach mit `--tag wdh` wiederholen. Hilfe: `python3 schneehoehensensor/skripte/mb7574_kammerlog.py stufe --help`.

## Ablauf

Zwischen den Messungen bleibt der Sensor stromlos: im Dauerbetrieb heizt er sich mit 0,34 W über die Kammertemperatur. Jedes Einschalten ist damit ein Kaltstart.

1. `git pull`, dann `neu`. Den Namen des Laufordners ins Laborbuch.
2. Tischtest vor dem Einbau: `stufe 20 --tag tisch`.
3. Stufen: +20 °C → −40 → −50 → −60 → −70 → +20 °C. Je Stufe Solltemperatur erreicht, 30 min halten, dann `stufe <Solltemperatur>`.
4. Läuft bei −70 °C alles, nach weiteren 60 min `stufe -70 --tag 60min`. Die letzte Stufe `stufe 20 --tag ende`.

## Ausfall und Auffälligkeiten

Das Skript meldet als auffällig: Zyklen ohne Kopfzeile oder mit weniger als drei Werten, R5000 (kein Echo), ungültige Zeilen, Median von Zyklus 1 mehr als 2 % und Strom mehr als 20 % neben dem Bezug. Selbst ansehen: nur der Minimalwert, springende Werte (min/max).

Steigt der Median von Zyklus 1 unterhalb −40 °C stetig um etwa 0,2 % je K, misst der Sensor weiter, aber seine interne Temperaturkompensation folgt der Kammer nicht mehr. Das als Bemerkung eingeben und weitermachen.

Bei Ausfall (keine oder unbrauchbare Werte):

1. Netzteil ein, Ader 7 (weiß) gegen Ader 4 (schwarz) messen. Etwa 1 mV je mm Distanz bedeutet: der Sensor misst, nur die serielle Ausgabe fällt aus.
2. Nach 15 min die Stufe mit `--tag wdh` wiederholen.
3. Bleibt er aus: nicht weiter abkühlen, sondern 10 K wärmer, 30 min halten, Stufe messen. Läuft er dort wieder, liegt die Grenze dazwischen; optional 5 K kälter nachmessen.
4. Danach auf +20 °C.

## Abschluss

1. Kammer öffnen, Wandler, Verguss und Kabel ansehen (Wasser, Risse), Fotos.
2. `abschliessen`, `notizen.md` ausfüllen, Fotos nach `fotos/` und abfotografierte Laborbuchseiten nach `scans/` im Laufordner, erneut `abschliessen`.
