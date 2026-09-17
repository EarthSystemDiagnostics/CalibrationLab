# Schneehöhensensor MaxBotix MB7574

Frage: Überlebt der MB7574 Plateau-Wintertemperaturen bis −70 °C und liefert er dort plausible Distanzwerte? Datenblatt: MaxBotix 12593 (SCXL-/HRXL-MaxSonar-WRS).

| Ordner | Inhalt |
|---|---|
| `aufbau/` | Laboraufbau: Aderbelegung, Stecklage, Prüfung vor dem Einschalten |
| `anleitungen/` | `Kammertest_MB7574.md` |
| `kammer` | Kurzaufruf des Loggers: `./kammer neu`, `./kammer stufe -40`, `./kammer abschliessen` |
| `klima` | Klimakammer über LAN: `./klima status`, `./klima stufe -40` (setzen, warten, 30 min halten, melden) |
| `skripte/` | `mb7574_kammerlog.py` (Logger), `klimakammer.py` (Kammer), `ex355p.py` (Netzteil EX355P) |
| `tests/` | `test_kammerlog.py`, `test_klimakammer.py`, ohne Hardware |
| `laeufe/` | ein Ordner je Kammertest |

Laborbuch: „Laborbuch Schneehöhensensor 1“.

## Läufe

| Lauf | Ergebnis |
|---|---|
| (noch keine) | |
