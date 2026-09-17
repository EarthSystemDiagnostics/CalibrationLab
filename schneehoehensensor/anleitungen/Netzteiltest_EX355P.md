# Netzteiltest EX355P — ohne Sensor

Stand 17.09.2026. Prüft die automatische Netzteilsteuerung (`skripte/ex355p.py`, `./kammer … --netzteil`), bevor ein Sensor daran hängt. Wichtigste Frage: Kann die Spannung am Ausgang über 5,00 V gehen?

## Sicherungen im Code

- Vor jedem Einschalten liest das Skript `V?` und `I?` zurück. Weichen sie von 5,00 V / 0,15 A ab, bleibt der Ausgang aus. Zu Beginn jeder Stufe setzt es beide Werte bei ausgeschaltetem Ausgang neu.
- Während EIN liest es 2 s nach dem Einschalten und dann alle 6 s `VO?`. Über 5,3 V schaltet es sofort aus und bricht ab (MB7574: 2,7–5,5 V).
- Andere Sollspannungen als 5,00 V lehnt es ab. `*RST` sendet es nie.
- Nicht abgedeckt: ein Hardwaredefekt im Netzteil zwischen zwei Ablesungen. Dagegen hilft nur ein Schutz am Sensor, z. B. eine Schutzdiode zwischen V+ und GND.

## Aufbau

- Sensorkabel (4-mm-Stecker) aus dem Netzteil ziehen.
- Multimeter (Gleichspannung) an den Ausgang. Falls vorhanden, Widerstand 68 Ω, ≥ 1 W parallel: zieht etwa 70 mA wie der Sensor und lässt die Spannung nach AUS schnell abfallen.
- Netzteil am Delock-Adapter `/dev/cu.usbserial-FT3GCNKB0`, 9600 Baud (am Netzteil eingestellt).
- Terminal: `cd ~/CalibrationLab/schneehoehensensor`, dann `git pull`.

## Tests

**1. Nur lesen**

```
python3 skripte/ex355p.py
```

Erwartet: Kennung `Thurlby Thandar,EX355P,0,v2.00`, 5,00 V / 0,15 A, Ausgang AUS, Multimeter etwa 0 V.

**2. Falsche Einstellung wird überschrieben**

„Go to Local“ drücken, von Hand etwa 12 V und 1 A einstellen, Ausgang **nicht** einschalten. Dann:

```
python3 skripte/ex355p.py --test
```

Erwartet: „Vorher eingestellt: 12.00 V, 1.00 A“, dann „Gesetzt und zurückgelesen: 5.00 V, 0.15 A“. Dreimal EIN (5 s) und AUS (3 s): bei EIN Multimeter 5,0 V, Skript etwa 5,0 V und mit Widerstand etwa 70 mA; bei AUS etwa 0 V (ohne Widerstand bis 2 s). Am Ende „Ende: Ausgang AUS“.

Bestanden, wenn das Multimeter nie mehr als 5,05 V zeigt.

**3. Knöpfe gesperrt**

Während Test 2 läuft, am Spannungsknopf drehen. Es darf sich nichts ändern.

**4. Abbruch schaltet aus**

`python3 skripte/ex355p.py --test` starten, während einer EIN-Phase Ctrl-C drücken. Multimeter fällt auf etwa 0 V; `python3 skripte/ex355p.py` zeigt danach „Ausgang AUS“.

**5. Kompletter Ablauf wie in der Messung**

In einem Testordner unter `/tmp`, damit der echte Laufordner unberührt bleibt. Bei `neu` die Fragen mit Enter beantworten.

```
./kammer --laeufe /tmp/netzteiltest neu
./kammer --laeufe /tmp/netzteiltest stufe 20 --netzteil --ein 5 --aus 3 --keine-bemerkung
```

Erwartet: dreimal EIN/AUS am Multimeter, „Strom laut Netzteil …“, am Ende Ausgang AUS. „AUFFÄLLIG“ wegen fehlender Sensordaten ist hier richtig.

## Danach

Sensorkabel wieder einstecken. Für Handbetrieb „Go to Local“ drücken. Ergebnis mit Datum ins Laborbuch Schneehöhensensor 1.
