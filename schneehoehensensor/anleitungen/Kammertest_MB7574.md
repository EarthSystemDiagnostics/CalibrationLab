# Kammertest MB7574 — Kälteüberleben

Stand 14.09.2026. Alle Beobachtungen mit Uhrzeit ins „Laborbuch Schneehöhensensor 1“. Allgemeines Vorgehen: `SOP_Labortests.md` in der Repo-Wurzel.

## Ziel

Bis zu welcher Kammertemperatur startet der MB7574 und liefert plausible Distanzwerte, und kommt er nach einem Ausfall beim Aufwärmen zurück? Temperatur ist die Kammeranzeige (±0,5 °C); einen eigenen Fühler gibt es nicht.

## Aufbau

- Verdrahtung, Stecklage und Prüfung vor dem Einschalten: `../aufbau/Laboraufbau_MB7574.md`.
- Sensor starr montiert, Strahl rechtwinklig auf eine ebene Fläche in 0,6–1 m Abstand (Kammerwand, Boden oder Platte). Den Aufbau bis Testende nicht verändern.
- Netzteil 5,00 V, Strombegrenzung 150 mA.

## Ablauf

Zwischen den Messungen bleibt der Sensor stromlos: im Dauerbetrieb heizt er sich mit 0,34 W über die Kammertemperatur. Jedes Einschalten ist damit ein Kaltstart.

Alle Befehle im Repo-Ordner. Vor der ersten Stufe:

1. `git pull`
2. `python3 schneehoehensensor/skripte/mb7574_kammerlog.py neu` fragt Personen, Seriennummer und Beschreibung ab und legt den Laufordner an. Seinen Namen ins Laborbuch.
3. Tischtest vor dem Einbau: `python3 schneehoehensensor/skripte/mb7574_kammerlog.py stufe 20 --tag tisch`

Stufen: +20 °C → −40 → −50 → −60 → −70 → +20 °C. Je Stufe:

1. Solltemperatur erreicht, 30 min halten.
2. Solltemperatur einsetzen: `python3 schneehoehensensor/skripte/mb7574_kammerlog.py stufe -40`
3. Netzteil nach Ansage schalten, dreimal 20 s EIN, 10 s AUS. Im ersten EIN den Strom am Netzteil ablesen.
4. Am Ende Strom und Bemerkung eingeben. Das Skript zeigt Zyklen mit Daten und Kopfzeile, Median und die Abweichung zur ersten +20-°C-Stufe, markiert Auffälligkeiten und schreibt Log und Zusammenfassung in den Laufordner.

Läuft bei −70 °C alles, nach weiteren 60 min wiederholen mit `--tag 60min`. Die letzte +20-°C-Stufe mit `--tag ende`.

## Ausfall und Auffälligkeiten

Das Skript meldet als auffällig: Zyklen ohne Kopfzeile oder mit weniger als drei Werten, R5000 (kein Echo), ungültige Zeilen, Median mehr als 2 % und Strom mehr als 20 % neben dem +20-°C-Wert. Selbst ansehen: nur der Minimalwert, springende Werte (min/max).

Steigt der Median unterhalb −40 °C stetig um etwa 0,2 % je K, misst der Sensor weiter, aber seine interne Temperaturkompensation folgt der Kammer nicht mehr. Das als Bemerkung eingeben und weitermachen.

Bei Ausfall (keine oder unbrauchbare Werte):

1. Netzteil ein, Ader 7 (weiß) gegen Ader 4 (schwarz) messen. Etwa 1 mV je mm Distanz bedeutet: der Sensor misst, nur die serielle Ausgabe fällt aus.
2. Nach 15 min die Stufe mit `--tag wdh` wiederholen.
3. Bleibt er aus: nicht weiter abkühlen, sondern 10 K wärmer, 30 min halten, Stufe messen. Läuft er dort wieder, liegt die Grenze dazwischen; optional 5 K kälter nachmessen.
4. Danach auf +20 °C.

## Abschluss

1. Bei +20 °C nach 30 min `stufe 20 --tag ende`: Werte und Strom wie zu Beginn?
2. Kammer öffnen, Wandler, Verguss und Kabel ansehen (Wasser, Risse), Foto.
3. `python3 schneehoehensensor/skripte/mb7574_kammerlog.py abschliessen` legt `notizen.md` an. Ausfüllen, Fotos nach `fotos/` und abfotografierte Laborbuchseiten nach `scans/` im Laufordner, dann erneut `abschliessen`: checkt den Laufordner ein und pusht.
