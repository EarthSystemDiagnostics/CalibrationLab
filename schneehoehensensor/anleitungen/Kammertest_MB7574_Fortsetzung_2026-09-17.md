# Kammertest MB7574 — Fortsetzung Donnerstag 17.09.2026

Lauf `kammer_MB7574_20260914-170058`. Montag: −20 °C, 3 von 3 Kaltstarts, Median 984 mm, 92 mA. Heute: Bezugsstufe +20 °C, Kältestufen, am Ende Abstand zum Ziel. Beobachtungen mit Uhrzeit ins Laborbuch Schneehöhensensor 1.

## 1. Vorbereitung

1. Laborbuch: Datum, Personen.
2. Der Sensor hat keine Seriennummer: Kennung `MB7574-01` (PartsBox-Label oder Folienstift) und ins Laborbuch.
3. Aufbau seit Montag unverändert lassen: Sensor und Ziel nicht bewegen, damit die −20-°C-Stufe vom Montag vergleichbar bleibt. Weiter im selben Lauf, **kein** `./kammer neu`.
4. Netzteil 5,00 V, Strombegrenzung 150 mA, Ausgang AUS. CoolTerm geschlossen.
5. Der Stützkondensator ist nicht eingebaut; so ins Laborbuch.

## 2. Terminal

Terminal öffnen: Cmd-Leertaste, „Terminal“ tippen, Enter. Befehle eintippen und mit Enter ausführen. Das Terminal arbeitet immer in einem Ordner:

| Befehl | macht |
|---|---|
| `pwd` | zeigt, in welchem Ordner man gerade ist |
| `cd Ordner` | wechselt in den Ordner; `cd ..` geht eine Ebene zurück |
| `ls` | listet, was im Ordner liegt |
| Tab-Taste | ergänzt angefangene Ordner- und Dateinamen |
| Pfeil nach oben | holt den letzten Befehl zurück, zum Ändern und erneut Ausführen |
| Ctrl-C | bricht einen laufenden Befehl ab |

Einmal zu Beginn:

```
cd ~/CalibrationLab/schneehoehensensor
pwd
git pull
ls laeufe
```

- `pwd` muss auf `CalibrationLab/schneehoehensensor` enden. Heißt der Ordner des Klons anders: `cd ` tippen, den Ordner `schneehoehensensor` aus dem Finder ins Terminal ziehen, Enter.
- `ls laeufe` muss `kammer_MB7574_20260914-170058` zeigen.

Ab jetzt bleibt das Terminal in diesem Ordner.

## 3. Bezugsstufe +20 °C

+20 °C, nach Erreichen 30 min halten. Dann:

```
./kammer stufe 20
```

1. Das Skript nennt Lauf und Logdatei, dann piept es: „Zyklus 1/3: Netzteil EIN (Strom ablesen)“. Netzteil einschalten, Strom ablesen. Unten läuft „letzte Zeile: R0984“ mit.
2. „Netzteil AUS“: ausschalten. Das dreimal, dazwischen je 10 s Pause; nach dem dritten AUS geht es sofort weiter. Zusammen etwa 1,5 min.
3. „Strom während EIN in mA“: Zahl eingeben, z. B. `72`, Enter.
4. „Bemerkung“: Text oder nur Enter.
5. Zusammenfassung lesen; steht dort „AUFFÄLLIG“, ins Laborbuch.

Diese Stufe ist der Bezug für alle folgenden. War dabei CoolTerm verbunden oder fehlt etwas (weniger als 3/3 Zyklen, ungültige Zeilen): CoolTerm trennen, 15 min warten, dann

```
./kammer stufe 20 --tag bezug
```

Diese Stufe gilt dann als Bezug; die erste bleibt als Rohdatei erhalten. Ins Laborbuch, warum wiederholt wurde.

## Klimakammer per Skript

Zweites Terminal-Fenster öffnen (Cmd-N):

```
cd ~/CalibrationLab/schneehoehensensor
git pull
./klima status
```

Für Hinweise aufs Handy einmalig: App „ntfy“ installieren, Thema aus dem Laborbuch abonnieren (Android: Akku-Optimierung für ntfy aus), am Labor-Mac `echo <thema> > ~/.klima_ntfy`, testen mit `curl -d "Test" ntfy.sh/<thema>`.

`./klima status` muss Ist, Soll und „läuft“ zeigen. Das Setzen des Sollwerts ist an der echten Kammer noch nicht erprobt: beim ersten `./klima stufe` am Panel nachsehen, ob der neue Sollwert ankommt.

Je Stufe in diesem Fenster, z. B.:

```
./klima stufe -50
```

Mit `j` bestätigen. Das Skript meldet die voraussichtliche Uhrzeit und gibt eine Mac-Mitteilung beim Erreichen, 10 min vor Ende und nach 30 min („Stufe messen“). Dann im ersten Fenster die Stufe messen. Ctrl-C beendet das Warten, der Sollwert bleibt.

## 4. Kältestufen

Je Stufe: `./klima stufe <Zahl>` im zweiten Fenster, bei „Stufe messen“ im ersten Fenster die passende Zeile. Ablauf wie in Abschnitt 3. Schneller: Pfeil nach oben, Zahl ändern, Enter.

```
./kammer stufe -40
./kammer stufe -50
./kammer stufe -60
./kammer stufe -70
```

Läuft −70 °C, 60 min später:

```
./kammer stufe -70 --tag 60min
```

Wird der Test heute nicht fertig: aufhören. Am nächsten Tag Terminal öffnen, `cd ~/CalibrationLab/schneehoehensensor`, mit der nächsten Stufe weitermachen; sie landet wieder in diesem Laufordner.

## 5. Bei Ausfall

1. Netzteil ein, Multimeter auf Gleichspannung: rot an Ader 3 (braun), schwarz an Ader 4 (schwarz). Liegt die Spannung deutlich unter 5 V, zuerst Netzteil, Stecker und Kabel prüfen.
2. Rote Messleitung auf Ader 7 (weiß) umstecken. Etwa 1 mV je mm Abstand: Der Sensor misst, nur die serielle Ausgabe fällt aus.
3. Nach 15 min die Stufe wiederholen, Beispiel für −60 °C:

```
./kammer stufe -60 --tag wdh
```

4. Bleibt er aus: nicht weiter abkühlen, sondern 10 K wärmer, 30 min halten, Stufe messen.

Eine laufende Stufe bricht Ctrl-C ab; danach mit `--tag wdh` wiederholen.

## 6. Ende des Kammertests

Auf +20 °C, nach Erreichen 30 min halten, letzte Stufe:

```
./kammer stufe 20 --tag ende
```

1. Kammer öffnen, Sensor und Ziel nicht bewegen: Abstand von der Stirnfläche des Sensors bis zur Zielfläche mit dem Maßband auf 5 mm messen, ins Laborbuch, Foto mit Maßband.
2. Wandler, Verguss und Kabel ansehen, Fotos.
3. Fotos und abfotografierte Laborbuchseiten per Mail an Thomas, Betreff `kammer_MB7574_20260914-170058`. Er legt sie in den Laufordner.
4. Notizen anlegen und öffnen:

```
./kammer abschliessen
open -e laeufe/kammer_MB7574_20260914-170058/notizen.md
```

5. In `notizen.md` eintragen und speichern: Laborbuchseiten; Kennung `MB7574-01`, keine Seriennummer vorhanden; gemessener Abstand; Abweichungen „Montag −20 °C ohne vorherige +20-°C-Stufe“ und „ohne Stützkondensator“; „Fotos per Mail an Thomas“; Ergebnis in einem Satz.
6. Einchecken und pushen; das Skript zeigt die Dateiliste, mit `j` bestätigen:

```
./kammer abschliessen
```
