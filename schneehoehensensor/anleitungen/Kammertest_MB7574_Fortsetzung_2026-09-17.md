# Kammertest MB7574 — Fortsetzung Donnerstag 17.09.2026

Lauf `kammer_MB7574_20260914-170058`. Montag: −20 °C, 3 von 3 Kaltstarts, Median 984 mm, 92 mA. Heute: Abstand zum Ziel messen, Bezugsstufe +20 °C, dann die Kältestufen. Beobachtungen mit Uhrzeit ins Laborbuch Schneehöhensensor 1.

## 1. Vorbereitung

1. Laborbuch: Datum, Personen.
2. Der Sensor hat keine Seriennummer: Kennung `MB7574-01` mit Folienstift aufs Gehäuse und ins Laborbuch.
3. Aufbau seit Montag unverändert? Dann weiter im selben Lauf, **kein** `neu`. Wurden Sensor oder Ziel neu montiert: in Abschnitt 2 zusätzlich `neu` ausführen und das im Laborbuch vermerken.
4. Kammer offen: Abstand von der Stirnfläche des Sensors bis zur Zielfläche mit dem Maßband auf 1 mm messen, ins Laborbuch, Foto mit Maßband.
5. Netzteil 5,00 V, Strombegrenzung 150 mA, Ausgang AUS. CoolTerm geschlossen.
6. Der Stützkondensator ist nicht eingebaut; so ins Laborbuch. Multimeter auf Gleichspannung: rot an Ader 3 (braun), schwarz an Ader 4 (schwarz). Es bleibt während der Stufen angeschlossen und zeigt die Spannung am Sensor.

## 2. Terminal

In den Ordner des Klons wechseln; heißt er anders, nach `cd ` den Ordner aus dem Finder ins Terminal ziehen.

```
cd ~/CalibrationLab
git pull
ls schneehoehensensor/laeufe/
```

Die Liste muss `kammer_MB7574_20260914-170058` zeigen; er ist der neueste Laufordner.

## 3. Bezugsstufe +20 °C

Kammer schließen, +20 °C, nach Erreichen 30 min halten. Dann:

```
python3 schneehoehensensor/skripte/mb7574_kammerlog.py stufe 20
```

1. Das Skript nennt Lauf und Logdatei, dann piept es: „Zyklus 1/3: Netzteil EIN (Strom ablesen)“. Netzteil einschalten, Strom am Netzteil und Spannung am Multimeter ablesen. Unten läuft „letzte Zeile: R0984“ mit.
2. „Netzteil AUS“: ausschalten. Das dreimal, dazwischen je 10 s Pause; nach dem dritten AUS geht es sofort weiter. Zusammen etwa 1,5 min.
3. „Strom während EIN in mA“: Zahl eingeben, z. B. `72`, Enter.
4. „Bemerkung“: Spannung eingeben, z. B. `U 4,98 V`, dazu Beobachtungen.
5. Zusammenfassung lesen; steht dort „AUFFÄLLIG“, ins Laborbuch.

Diese Stufe hat keinen `--tag` und ist damit der Bezug für alle folgenden.

## 4. Kältestufen

Je Stufe: Solltemperatur einstellen, nach Erreichen 30 min halten, dann die passende Zeile. Ablauf wie in Abschnitt 3.

```
python3 schneehoehensensor/skripte/mb7574_kammerlog.py stufe -40
python3 schneehoehensensor/skripte/mb7574_kammerlog.py stufe -50
python3 schneehoehensensor/skripte/mb7574_kammerlog.py stufe -60
python3 schneehoehensensor/skripte/mb7574_kammerlog.py stufe -70
```

Läuft −70 °C, 60 min später:

```
python3 schneehoehensensor/skripte/mb7574_kammerlog.py stufe -70 --tag 60min
```

Wird der Test heute nicht fertig: aufhören und am nächsten Tag mit der nächsten Stufe weitermachen; sie landet wieder in diesem Laufordner.

## 5. Bei Ausfall

1. Netzteil ein, Spannung Ader 3 gegen Ader 4 ablesen. Liegt sie deutlich unter 5 V, zuerst Netzteil, Stecker und Kabel prüfen; dann liegt die Ursache in der Versorgung.
2. Rote Messleitung von Ader 3 auf Ader 7 (weiß) umstecken, schwarz bleibt an Ader 4. Etwa 1 mV je mm Abstand: Der Sensor misst, nur die serielle Ausgabe fällt aus.
3. Nach 15 min die Stufe wiederholen, Beispiel für −60 °C:

```
python3 schneehoehensensor/skripte/mb7574_kammerlog.py stufe -60 --tag wdh
```

4. Bleibt er aus: nicht weiter abkühlen, sondern 10 K wärmer, 30 min halten, Stufe messen.

Eine laufende Stufe bricht Ctrl-C ab; danach mit `--tag wdh` wiederholen.

## 6. Ende des Kammertests

Auf +20 °C, nach Erreichen 30 min halten, letzte Stufe:

```
python3 schneehoehensensor/skripte/mb7574_kammerlog.py stufe 20 --tag ende
```

Kammer öffnen, Wandler, Verguss und Kabel ansehen, Fotos. Dann Notizen anlegen und öffnen:

```
python3 schneehoehensensor/skripte/mb7574_kammerlog.py abschliessen
open -e schneehoehensensor/laeufe/kammer_MB7574_20260914-170058/notizen.md
```

In `notizen.md` eintragen und speichern: Laborbuchseiten; Kennung `MB7574-01`, keine Seriennummer vorhanden; gemessener Abstand; Abweichungen „Montag −20 °C ohne vorherige +20-°C-Stufe“ und „ohne Stützkondensator“; Ergebnis in einem Satz.

Fotos und abfotografierte Laborbuchseiten in den Laufordner, iPhone-Bilder dabei umwandeln (Dateinamen anpassen):

```
cd schneehoehensensor/laeufe/kammer_MB7574_20260914-170058
mkdir -p fotos scans
sips -s format jpeg -Z 1600 ~/Downloads/IMG_1234.HEIC --out fotos/IMG_1234.jpg
sips -s format jpeg -Z 1600 ~/Downloads/IMG_1235.HEIC --out scans/IMG_1235.jpg
cd ~/CalibrationLab
```

Einchecken und pushen; das Skript zeigt die Dateiliste, mit `j` bestätigen:

```
python3 schneehoehensensor/skripte/mb7574_kammerlog.py abschliessen
```
