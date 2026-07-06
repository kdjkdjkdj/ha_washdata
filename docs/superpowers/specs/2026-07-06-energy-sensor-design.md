# Design: Optionaler `energy_sensor` als native Energie-Quelle

**Datum:** 2026-07-06
**Repo:** Fork `kdjkdjkdj/ha_washdata` (Upstream `3dg1luk43/ha_washdata`)
**Ziel:** Upstream-PR-taugliches Feature. Dieses Spec-Dokument ist intern und wird vor dem Upstream-PR aus dem Branch entfernt.

## Problem

WashData berechnet die Zyklus-Energie (`cycle_data["energy_wh"]`) ausschließlich durch Trapez-Integration der gesampelten Leistungskurve (`manager.py::_on_cycle_end`, Fallback in `profile_store.py::~1343`). Smart Plugs (Shelly, Tasmota) liefern Leistung report-on-change — Spitzen zwischen zwei Reports gehen verloren, die Integration unterschätzt systematisch. Real gemessen: ~23 % zu niedrig (Tiny, Waschmaschinenlauf 2026-07-05: WashData 0,337 kWh vs. Plug-Zähler ~0,44 kWh). Der native Energiezähler des Plugs integriert on-device hochfrequent und ist die korrekte Referenz.

## Lösung (Ansatz „Start/Ende-Snapshots")

Neue optionale Konfiguration `energy_sensor` je Gerät. Beim Zyklusstart und -ende wird der Zählerstand gelesen; das Delta ersetzt den integrierten Wert in `cycle_data["energy_wh"]`. Die Integration bleibt vollständig erhalten und dient als Fallback sowie weiterhin als Eingang für die Ghost-Cycle-/Pump-out-Filter.

Verworfene Alternativen:
- **Kontinuierlicher State-Listener** (Deltas laufend aufsummieren): robuster gegen Zähler-Resets mitten im Zyklus, aber deutlich mehr Code bei gleicher Endgenauigkeit (begrenzt durch Report-Kadenz des Zählers). Resets mitten im Lauf sind praktisch selten (Plug-Firmware-Neustart).
- **Kalibrierfaktor lernen**: bleibt eine Schätzung, Fehler variiert je Lastprofil; schwer als Upstream-PR zu argumentieren.

## Komponenten & Änderungen

### 1. `const.py`
- `CONF_ENERGY_SENSOR = "energy_sensor"` (kein Default; `None` = Feature aus).

### 2. `config_flow.py`
- **Setup-Schritt** (dort, wo `CONF_POWER_SENSOR` als Required-EntitySelector steht, ~Z. 222): `vol.Optional(CONF_ENERGY_SENSOR)` als `selector.EntitySelector` (Domain `sensor`, device_class `energy`).
- **Options-Flow**: an derselben Stelle editierbar, an der heute `power_sensor` änderbar ist (~Z. 528/561). Leeren-Handling nach dem Vorbild `door_sensor_entity` (~Z. 1028–1030): leerer Wert → `None`.

### 3. `manager.py`
- **Zyklusstart** (im „neuer Zyklus"-Block, ~Z. 2609, wo `_cycle_start_time` gesetzt wird):
  Zählerstand lesen und nach Wh normalisieren → `self._energy_counter_start_wh: float | None`.
  Einheiten-Normierung über `unit_of_measurement`: `Wh` ×1, `kWh` ×1000, `MWh` ×1_000_000. Fehlende/unbekannte Einheit oder nicht-numerischer Zustand → `None` (Fallback), Debug-Log.
- **Persistenz**: `energy_counter_start_wh` in den Active-Cycle-Snapshot aufnehmen (gleicher Mechanismus wie `notified_start`/`start_event_fired`, ~Z. 1813–1822) und beim Restore zurücklesen (~Z. 1234 ff.). Fehlt der Key im restaurierten Snapshot (Zyklus lief vor dem Update an) → `None`.
- **Zyklusende** (`_on_cycle_end`):
  1. Trapez-Integration läuft unverändert (speist Ghost-Cycle-Check ~Z. 2710 und Pump-out-Suppression ~Z. 2723 — deren Schwellwerte sind auf integrierte Werte getunt und bleiben unangetastet).
  2. Danach Zähler erneut lesen → `delta_wh = end - start`. Gültig, wenn: Start-Snapshot vorhanden, Endwert numerisch mit bekannter Einheit, `delta_wh >= 0`.
  3. Gültig → `cycle_data["energy_wh"] = round(delta_wh, 3)` und `cycle_data["energy_source"] = "meter"`.
     Ungültig → integrierter Wert bleibt, `energy_source = "integration"`, Log (Reset/negativ: Info; unavailable/Einheit: Debug).
  4. `self._energy_counter_start_wh = None` beim Zyklusende und beim Übergang zu `STATE_OFF` zurücksetzen.
- **Unangetastet**: Notification-Pfad (liest `cycle_data.energy_wh`, ~Z. 2844, bekommt automatisch den besseren Wert); Erkennungslogik/`cycle_detector` komplett; `profile_store`-Fallback (füllt nur, wenn `energy_wh` fehlt).

### 4. `strings.json` + `translations/en.json`
- Label + Beschreibung für das neue Feld in Setup- und Options-Schritt. Andere Sprachen: nicht anfassen (HA fällt auf Englisch zurück; Validierungsreferenz ist `en`).

### 5. Doku
- README: Absatz zur Option (was, warum, Grenzen).
- CHANGELOG: Feature-Eintrag im Upstream-Stil.

## Fehlerfälle (vollständig)

| Fall | Verhalten |
|---|---|
| `energy_sensor` nicht konfiguriert | heutiges Verhalten, kein Log |
| Zustand `unavailable`/`unknown`/nicht numerisch (Start oder Ende) | Fallback Integration, Debug-Log |
| Einheit fehlt/unbekannt | Fallback Integration, Debug-Log (nichts raten) |
| `delta_wh < 0` (Zähler-Reset im Zyklus) | Fallback Integration, Info-Log |
| HA-Neustart mitten im Zyklus | Startwert aus persistiertem Snapshot; fehlt er → Fallback |
| Recorder-Zyklen / nachträglich getrimmte Zyklen | trace-basierte Energie bleibt (Zeitfenster-Mismatch beim Trim); dokumentierte Grenze |

Akzeptierte Restungenauigkeit: Report-Kadenz des Energiezählers (Sekunden bis ~1 min Verzug am Zyklusende) — weit unter den bisherigen −23 %.

## Tests (pytest, testgetrieben)

- Einheiten-Normierung: Wh, kWh, MWh, fehlende Einheit, nicht-numerisch.
- Happy Path: gültige Snapshots → `energy_wh` = Delta, `energy_source = "meter"`, Integration unangetastet für Ghost-Check.
- Reset-Fallback: `delta_wh < 0` → integrierter Wert, `energy_source = "integration"`.
- Unavailable-Fallback: Sensor bei Start bzw. Ende unavailable.
- Persistenz-Roundtrip: Snapshot speichern/restaurieren erhält `energy_counter_start_wh`; Alt-Snapshot ohne Key → Fallback.

## Deployment & Verifikation

1. Feature-Branch `feature/energy-sensor` im Fork; Commits mit Co-Author-Trailer (Upstream-PR ist Ziel).
2. Nach grüner Suite: Merge auf Fork-`main`, Release-Tag. Fork-Version in `manifest.json` muss für HACS **höher als die Upstream-Version** einsortiert werden (Pre-Release-Suffixe wie `-kdj.1` sortieren niedriger!) — genaues Schema im Implementierungsplan anhand der realen Upstream-Version festlegen (z. B. Patch-Bump).
3. HACS auf **HA-Tiny**: Fork als Custom Repository, Installation ersetzt Upstream-Version (Config-Entries bleiben, gleiche Domain).
4. `energy_sensor` zunächst nur **Waschmaschine Tiny** setzen; nach 2–3 Zyklen mit `energy_source = "meter"` und Plausibilitäts-Abgleich gegen den Shelly-Zähler auf Trockner/Spülmaschine ausrollen. **HA-KD bleibt auf Upstream**, bis der Test bestanden ist.
5. Danach: Upstream-PR vorbereiten (Spec-Commit droppen, Branch auf `upstream/main` rebasen); PR-Text vor dem Absenden im Chat freigeben lassen.
