# Übergabe: Kalibrier-Logging + Badsteuerung

**Von:** Thomas Laepple · **An:** Nora Hirsch · **Stand:** 24.08.2026
**Repo:** `EarthSystemDiagnostics/CalibrationLab`, Branch `bath-control-and-live-temps`

Der Code stammt ursprünglich von dir und Kathrin (MicroK- und NTC-Logging). Ich
habe ihn während der Grönland-Zeit erweitert — im Kern um **Badsteuerung** und um
**zeitgenaue Messung**. Diese Datei ist der Wiedereinstieg: was jetzt anders ist,
wie man einen Lauf fährt, und was offen ist. Die Details stehen in `README.md`
(Bedienung) und `DATA_FORMATS.md` (Dateiformate für die R-Pipeline).

---

## 1. Was neu ist gegenüber deiner Version

| Neu | Warum |
|---|---|
| `calibration_auto.py` — fährt das Bad selbst durch eine Plateau-Liste | kein manuelles Setzen mehr, 24-h-Läufe unbeaufsichtigt |
| Plateau-Ende über **Drift-Gate am SPRT** statt fester Verweildauer | ein Plateau ist fertig, wenn die Referenz steht, nicht wenn die Uhr abläuft |
| **Ein einziges Node-Array** statt Gruppen-Round-Robin | jedes Kommando startet den Kopf neu; das Umschalten hat den Stream zerhackt |
| **`TOFFMS`** — Zeitstempel pro Einzelwert in der NTC-Zeile | der Kopf misst seriell, eine Zeile dauert bis zu ~2 min; ein Zeitstempel pro Zeile aliast die Badschwingung in mehrere mK Sensorfehler |
| Live-Temperaturen auf dem Schirm (SPRT und alle NTC-Kanäle) | man sieht sofort, wo man steht; **nur Anzeige**, die Dateien enthalten weiter Rohwerte |
| `--dashboard`: feste Übersichtstafel statt scrollendem Log | für lange Läufe |
| `bisynch.py`, `bath.py`, `gate.py`, Testsuiten | Badprotokolle und Gate, offline testbar |

`calibration_log.py` (der passive Logger) ist weiter da und bleibt das Werkzeug,
wenn das Bad von Hand gesetzt wird. Beide Wege schreiben **dieselben** Dateien:
`calibration_auto.py` importiert die Logger-Funktionen aus `calibration_log.py`
unverändert und legt nur die Badsteuerung darüber.

---

## 2. Stand heute

- Die Arbeit ist am 24.08.2026 nach `main` gemerged (Fast-Forward, `main` hatte
  seit dem 02.07.2026 keine eigenen Commits). `main` und
  `bath-control-and-live-temps` zeigen auf denselben Stand — auschecken kannst
  du also einfach `main`.
- Offline-Tests laufen durch: `python3 test_bath.py` → 47 passed,
  `python3 test_bisynch.py` → 15 passed. Beide brauchen **keine Hardware**.
- Alles ist eingecheckt und auf `origin` gepusht, das Arbeitsverzeichnis ist
  sauber.
- Zuletzt real gefahren wurde die 5-Sensor-Konfiguration
  (`param_combined.txt`, Knoten 90–94) und der 24-h-Plan `param_24h.txt`.

---

## 3. Ein Lauf, Schritt für Schritt

```bash
python3 -m pip install -r requirements.txt      # pyserial + minimalmodbus
python3 tools/port_detect.py --list             # welcher Adapter ist wo?
```

1. **`param_combined.txt` anpassen** — oben die drei Ports (Substring reicht,
   z. B. `usbserial-FT3GCNKB0`), dann `experiment`, `ntc_nodes`, `ntc_readout`,
   und unten die Plateaus.
2. **Trockenlauf**: `python3 calibration_auto.py --dry-run` — verbindet sich mit
   dem Bad und liest PV/Setpoint/Output, **ohne** etwas zu verstellen. Wenn hier
   sinnvolle Zahlen kommen, stimmen Protokoll und Adresse.
3. **Lauf starten**: `python3 calibration_auto.py --dashboard`
   (bzw. `--param param_24h.txt --dashboard` für den 24-h-Plan).
   Beim Start: Ports bestätigen (Enter nimmt den Vorschlag) und eine
   **Freitext-Beschreibung** eingeben — die landet im `_meta.txt`.
4. **Laufen lassen.** Abbruch mit `Ctrl-C`: die Logger schließen sauber, das Bad
   bleibt auf seinem letzten Setpoint stehen (es wird nicht zurückgefahren).
5. Ausgabedateien liegen in `./Output/`, ein Satz pro Lauf mit gemeinsamem
   Zeitstempel im Namen. Sie dürfen **während** des Laufs kopiert werden, jede
   Zeile ist sofort auf Platte.

Nur das Bad ansteuern, ohne Logging: `bath.py` (Modbus) bzw. `bisynch.py`
(EI-Bisynch) haben eigene CLIs — siehe README.

---

## 4. Vier Dinge, die man verstanden haben muss

**(a) Das Bad hat zwei Controller auf einer Leitung.**
Libra 785: der **Eurotherm 3504** ist der eigentliche Regler und spricht
**EI-Bisynch, 7E1, Adresse 1** — darüber läuft die Steuerung (`bisynch.py`).
Der **Übertemperatur-Begrenzer** ist ein separates Gerät, **Modbus RTU, 8N1,
Slave 2** (`bath.py`). Wer nur Modbus probiert, bekommt bestenfalls den
Begrenzer ans Telefon und denkt, das Bad antwortet nicht. Umschalten über
`bath_protocol:` (Default `bisynch`).

**(b) Das Plateau endet über die Referenz, nicht über die Uhr.**
`gate_mode: drift` (Default): akzeptiert, sobald über die letzten
`gate_window_min` (30 min) der gefittete Drift < 3 mK **und** die Streuung
< 8 mK ist. `plateau_minutes` ist dann nur noch die **Mindest-Soakzeit**;
Deckel ist `plateau_minutes + gate_max_extra_min`, danach wird gemessen und das
Plateau als `gate_ok=False` **markiert**. Gemessen wird gegen den MicroK-SPRT,
weil das Bad-PV nur 10 mK auflöst und 3 mK Drift gar nicht sehen kann.
Das akzeptierte Fenster `[t_dwell_start, t_dwell_end]` in `_plateaus.txt` **ist**
das Mittelungsfenster. `gate_mode: fixed` stellt das alte Verhalten wieder her.

**(c) Ein Node-Array, ein Header.**
Der Kopf behält sein Node-Array im eigenen Speicher — über Sweeps und über
Neustarts hinweg. Deshalb wird `NODES …` **genau einmal** beim Start geschickt,
danach nur noch gelesen. Jedes Kommando startet den Kopf neu; das frühere
Umschalten zwischen Gruppen hat bei kurzen Arrays nur noch Header produziert.
`ntc_groups` ist ein **Altlast-Feld**: muss in der Param-Datei stehen, gruppiert
aber nichts mehr. Alle Zeilen tragen weiter `Group1;` (Rückwärtskompatibilität).

**(d) `TOFFMS` ist der Grund, warum die Kalibration jetzt sauberer wird.**
Fünftes `;`-Feld in jeder Datenzeile: Millisekunden-Versatz **pro Einzelwert**
gegenüber dem Zeilenzeitpunkt. In der Auswertung jeden NTC-Wert mit dem SPRT
**auf genau diesen Zeitpunkt interpoliert** paaren (nicht die Fenstermittel der
beiden Ströme getrennt bilden) — dann fällt die ~10 mK/2 min Badschwingung als
Gleichtakt heraus. Rezept steht am Ende von `DATA_FORMATS.md`.

---

## 5. Das Bad ansteuern — welche Kommandos es gibt

Drei Ebenen, von oben nach unten:

**(1) Im Lauf** macht `calibration_auto.py` alles selbst; du stellst nur
`plateaus`, `plateau_minutes` und `ramp_c_per_min` in der Param-Datei ein.

**(2) Von Hand, ohne Logging** — `bisynch.py` für die Libra 785 (der 3504):

```bash
python3 bisynch.py --port /dev/cu.usbserial-XXXX --scan            # Framing + Adresse finden (read-only)
python3 bisynch.py --port … --addr 1 --identify                     # alle Mnemonics lesen und gegen das Panel halten
python3 bisynch.py --port … --addr 1 --read PV                      # gemessene Temperatur
python3 bisynch.py --port … --addr 1 --read SL                      # Sollwert
python3 bisynch.py --port … --addr 1 --monitor --interval 5          # PV/SP/WSP/OP laufend
python3 bisynch.py --port … --addr 1 --write SL -15                  # SOLLWERT SETZEN -- das Bad fährt los
python3 bisynch.py --port … --addr 1 --write RR 1                    # Rampenbegrenzung 1 °C/min (0 = aus)
```

`bath.py` ist dasselbe in Modbus und erreicht an der Libra nur den
Übertemperatur-Begrenzer (Slave 2); für andere Isotech-Bäder mit Modbus-Regler
kann es alles: `--monitor`, `--set -40`, `--wait -40 --minutes 20`,
`--plateaus "-40;-20;0" --minutes 15`, `--scan` bei Kommunikationsproblemen.

**Kommandos sind Zwei-Buchstaben-Mnemonics** (EI-Bisynch, ASCII). Die
gebräuchlichen, alle mit `--read` lesbar:

| Mnemonic | Bedeutung |
|---|---|
| `PV` | gemessene Temperatur (process value) |
| `SL` | Sollwert — **der schreibbare**, den wir setzen |
| `SP` / `S1` / `S2` | Sollwert bzw. Sollwert 1/2 (je nach Konfiguration) |
| `WS` | Arbeits-Sollwert, also der rampenbegrenzte Zwischenwert |
| `OP` | Ausgangsleistung in % |
| `RR` | Rampenrate des Sollwerts (°C/min, 0 = aus) |
| `HS` / `LS` | Sollwert-Ober-/Untergrenze |
| `SM` | Sollwert-Auswahl / Modus |
| `XP`, `TI`, `TD` | PID: Proportionalband, Nachstellzeit, Vorhaltzeit |
| `HB`, `LB` | Cutback hoch/tief — der Anti-Überschwing-Hebel |
| `AT` | Auto-Tune |

Die ersten vier stehen in `bisynch.py` (`IDENTIFY_MNEMONICS`) mit Erklärung, die
PID-Zeile in `PID_TUNING.md`. Vollständig sind sie im **Eurotherm 3500 Series
Engineering Handbook, HA027988** dokumentiert.

**(3) Aus Python**: `BisynchBath` in `bisynch.py` bzw. `Bath` in `bath.py` haben
dieselbe Schnittstelle — `read_pv()`, `read_setpoint()`, `read_working_setpoint()`,
`read_output()`, `set_setpoint(T)`, `set_ramp_rate(c_per_min)`, `read_ramp_rate()`,
`wait_until_stable(target, tol, window_s)`, `close()`. `set_setpoint()` liest den
Wert zurück und meldet, wenn der Regler ihn nicht angenommen hat.

> **Lesen ist harmlos, Schreiben nicht.** `--read`, `--scan`, `--identify` und
> `--monitor` ändern nichts. `--write SL …` fährt das Bad. Der
> Übertemperatur-Begrenzer bleibt in jedem Fall aktiv — er ist ein eigenes Gerät
> und hört nicht auf diese Kommandos.

---

## 6. Auswertung / R-Pipeline

`DATA_FORMATS.md` ist die maßgebliche Beschreibung der vier Ausgabedateien
(`_microk`, `_ntc`, `_meta`, `_plateaus`) inklusive Lese-Rezept. Zwei Punkte für
die bestehenden R-Reader in `../CalibrationChains/lib/`:

- **Neue Dateien** (ab 07.07.2026) haben genau einen Header in Zeile 1 —
  `read_logger_2026()` liegt damit richtig, keine Änderung nötig.
- **Alte Dateien** mit mehreren Gruppen bleiben eine stille Falle: die Labels
  müssen aus dem nächstliegenden Header **derselben** Gruppe kommen, sonst
  bekommen Gruppe-2/3-Zeilen die Knotennamen von Gruppe 1.
- In beiden Fällen: beim Splitten auf höchstens 4 Felder gehen und im Datenblock
  alles nach einem weiteren `;` abschneiden, sonst hängt am letzten Zählwert
  `"; TOFFMS=0"`.

Die NTC-Temperaturen auf dem Schirm nutzen die universelle **Mittel-S4-Kurve**
(`ntc.py`, `NTC_MEAN_COEF`, ±0.03–0.05 °C für einen gesunden Sensor). Das ist
**keine** Sensorkalibration — die bleibt in R. Die SPRT-Anzeige ist eine
2-Punkt-Gerade mit den **H1-2025**-Fixpunktverhältnissen aus `sprt.py`; nach einer
Neukalibrierung müssen die im Gleichschritt mit `SPRTRtoT_NTCtoR.R` nachgezogen
werden.

---

## 7. Offen / nächste Schritte

1. **Badüberschwingen.** Das Bad überschwingt ~2 °C bei einem Sprung; wir halten
   es mit `ramp_c_per_min: 1` klein, das kostet Zeit. Der eigentliche Hebel ist
   die PID/Cutback/Auto-Tune des 3504 über dieselbe serielle Leitung — Plan,
   Parameter und Sicherheitsregeln in **`PID_TUNING.md`**. Erster Schritt ist rein
   lesend (`bisynch.py --read XP` usw.), nichts davon hängt am Kalibriercode.
2. **Zwei SPRTs** (`microk_channels: 1;2;3`) sind implementiert, in den letzten
   Läufen aber nicht benutzt — einmal prüfen, bevor du dich darauf verlässt.
   Das Gate nimmt sonst den ersten SPRT (`gate_channel:` setzt es explizit).
3. **Ein Lauf mit dem aktuellen Stand am echten Bad** — alles außer der
   PID-Optimierung ist gefahren, aber die letzten Doku- und Struktur-Änderungen
   sind seit dem 07.07.2026 nicht mehr am Gerät gegengeprüft.
4. Der Rest steht in `TODO.md`.

---

## 8. Fallstricke im Labor

- **Ein Programm pro Port.** Läuft die Kalibration, darf `bath.py`/`bisynch.py`/
  `port_detect.py` nicht parallel auf denselben Port — sonst „keine Kommunikation".
- **Portnamen ändern sich beim Umstecken.** Die Hints in der Param-Datei sind nur
  Vorauswahl; beim Start wird ohnehin bestätigt. `tools/port_detect.py --map
  param_combined.txt` zeigt die Zuordnung.
- **Antwortet das Bad nicht:** `bisynch.py --port … --scan` (read-only) sucht
  Framing und Adresse; erwartet werden 7E1/Adresse 1. Erst danach `--identify`,
  um die Mnemonics gegen das Frontpanel abzugleichen, und erst dann schreiben.
- **`--dry-run` vor jedem neuen Aufbau.** Wenn PV/SP plausibel zurückkommen,
  stimmen Protokoll, Adresse und (bei Modbus) die Wertecodierung.
- **Abgebrochenes Plateau hinterlässt im Drift-Modus keine Zeile** in
  `_plateaus.txt` (es wurde ja nichts akzeptiert) — das Fenster notfalls aus den
  Rohlogs rekonstruieren.
- **Offene Eingänge**: Rohwerte > 10 000 000 heißen „nicht angeschlossen" —
  nicht konvertieren, verwerfen.

---

## 9. Kontakt

Bei allem, was nach Protokoll-Archäologie riecht (Bisynch-Mnemonics, Gate-Logik,
`TOFFMS`), erst `README.md` und die Kopfkommentare der jeweiligen Datei lesen —
die Begründungen stehen dort, wo der Code steht. Wenn etwas nicht zusammenpasst,
melde dich; ich habe alles außer der PID-Optimierung selbst gefahren.
