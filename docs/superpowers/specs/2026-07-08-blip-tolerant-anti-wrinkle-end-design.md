# Design: Blip-toleranter graziöser Timeout (profil-unabhängiges Zyklusende)

**Datum:** 2026-07-08
**Repo:** Fork `kdjkdjkdj/ha_washdata`, Branch `feature/blip-tolerant-anti-wrinkle-end`
**Ziel:** Upstream-PR-tauglich. Spec-Doc ist fork-intern, wird vor dem PR gedroppt.

## Problem

Ein Zyklus **ohne Profil-Match** erreicht in der Knitterschutz-Phase kein sauberes Ende und wird stattdessen vom Watchdog `force_stop`t — mit später, aufgeblähter „fertig"-Meldung.

Real beobachtet (Tiny, Trockner, 2026-07-07): Lauf 22:53 → 02:53, **240 min** aufgezeichnet / `status: force_stopped`, `profile_name: null`, 2,971 kWh. Das echte Trocknen war ~110 min (Volllast ~2000–2700 W); danach ~130 min Knitterschutz-Getrommel (~170 W-Blips alle paar Minuten). WashData konnte das Ende nicht erkennen → 4-h-Watchdog → `force_stop`, Meldung ~2 h zu spät.

Trockner sind besonders betroffen (variable Ladung/Restfeuchte → keine stabile Solldauer, Profil-Matching prinzipiell schwach — „blind"). Waschmaschinen trifft es bei Sonderläufen ohne Match.

## Root Cause (Code-verifiziert)

- **`cycle_detector.py` Z.604–620:** `is_high = power >= stop_threshold_w`. Bei jedem `is_high` → **`self._time_below_threshold = 0.0`** (Reset). Die Knitterschutz-Blips (~170 W ≫ `stop_threshold_w`) resetten den Aus-Timer alle paar Minuten.
- **Folge:** Die graziöse Timeout-Bedingung (`self._time_below_threshold >= effective_off_delay`, Z.~1135) erreicht ihre Schwelle nie → die Maschine hängt in `STATE_ENDING`, bis ein Watchdog `force_stop`t.
- **Wichtig — der Zielpfad existiert bereits:** `_finish_cycle(termination_reason="timeout")` betritt bereits `STATE_ANTI_WRINKLE`, wenn `anti_wrinkle_enabled` gesetzt ist (Z.~1441–1448, Bedingung `termination_reason in {"timeout","smart"}`). Es fehlt also nur, den graziösen Timeout **erreichbar** zu machen.

Der bestehende `long_ending_tail`-Mechanismus (Z.~994–998: nach `_time_in_state >= 120 s` werden Spikes „kept in ENDING", `return`) hält die Maschine zwar in ENDING, **stoppt aber den Timer-Reset in Z.608 nicht** — deshalb reicht er allein nicht.

## Lösung — Ansatz A: Blip-toleranter Timeout

Anti-Crease-Blips im `ENDING`-Tail eines ungematchten Zyklus dürfen den Aus-Timer **nicht mehr resetten**. Dann läuft `_time_below_threshold` normal hoch → der bestehende Timeout feuert → `_finish_cycle("timeout")` → `STATE_ANTI_WRINKLE`. **Kein neuer State, keine Änderung an den matched-Pfaden.**

### Klassifikator
Ein `is_high`-Ereignis wird als **Anti-Crease-Blip** gewertet (⇒ `_time_below_threshold` NICHT resetten, Blip „durchreichen"), wenn **alle** Bedingungen zutreffen:
1. `self._config.anti_wrinkle_enabled` ist gesetzt (Gerät macht Knitterschutz — nur dann ist die „Blip = Anti-Crease"-Annahme gültig),
2. `self._state == STATE_ENDING` und `_time_in_state >= 120 s` (bestehendes `long_ending_tail`),
3. `self._expected_duration <= 0` (ungematcht — matched-Fälle bleiben unangetastet),
4. `power < crease_resume_threshold` (Blip bleibt unter dem Wiederaufnahme-Niveau),
5. Anti-Ghost: `self._cycle_max_power` belegt ein reales Programm (Heiz-/Schleuder-Niveau überschritten).

Andernfalls (`power >= crease_resume_threshold`, echte Wiederaufnahme) → bisheriges Verhalten: Reset + zurück zu `RUNNING`.

### Device-Tuning
- **Trockner/`washer_dryer`:** `crease_resume_threshold` **hoch** (~1000 W → nur echtes Heizen = Wiederaufnahme; 170-W-Tumbles = Anti-Crease). `unmatched_off_delay` **kurz** (~10–15 min ohne echte Aktivität → Ende). Aggressiv, da keine Soak-Phasen.
- **Waschmaschine:** konservativ. `crease_resume_threshold` niedriger, `unmatched_off_delay` deutlich länger. **Zusätzliche Schleuder-Sicherung:** Blip-toleranter Finish greift nur, **nachdem eine Schleuder-Phase gesehen wurde** (`_cycle_max_power` über Schleuder-Niveau). Ein Soak mitten im Programm ist nie von einem Endschleudern gefolgt → kein verfrühtes „fertig". (Trockner braucht diese Sicherung nicht — Heizen davor genügt.)

### Verworfene Alternativen
- **B — expliziter Anti-Crease-Muster-Detektor** (Periodizität der Pulse erkennen): am spezifischsten, aber komplex und hohe Testfläche/Regressionsrisiko. Für später offen, falls A in der Praxis zu grob ist.
- **C — nur Trockner, „Heizen gestoppt für T min"**: trivial und sicher, hilft der WM aber nicht. Bleibt als Notnagel, falls A zu heikel wird.
- **Ersetzen statt Augmentieren** (ein Detektor für alle Fälle): höheres Regressionsrisiko für die heute funktionierenden matched-Pfade; verworfen.
- **Neues Enable-Flag** statt `anti_wrinkle_enabled`: unnötige Config-Fläche; das bestehende Flag ist der semantisch korrekte Gate.

## Änderungen

### `cycle_detector.py`
- **Reset-Stelle Z.604–620:** Der `is_high`-Zweig darf `_time_below_threshold` nicht resetten, wenn der Klassifikator (s. o.) einen Anti-Crease-Blip erkennt. Umsetzung sauber isoliert, z. B. Hilfsmethode `self._is_anti_crease_blip(power)` als Bedingung vor dem Reset in Z.608 (und Fortführung der Energie-/`_last_active_time`-Buchung wie im Nicht-`is_high`-Fall, damit der Blip nicht als „Aktivität" zählt).
- **`unmatched_off_delay`** im Timeout-Gate (Z.~1116/1135) berücksichtigen: bei `_expected_duration <= 0` + `anti_wrinkle_enabled` das device-getunte `unmatched_off_delay` statt des großen Standard-`off_delay` verwenden.
- Die Schleuder-Sicherung der WM als Teil des Klassifikators (Bedingung 5 / device-spezifisch).

### `const.py`
- Neue Config-Konstanten + Defaults: `CONF_CREASE_RESUME_THRESHOLD`, `CONF_UNMATCHED_OFF_DELAY` (device-type-abhängige Defaults; oder ein Default + Suggestions-Logik wie bei bestehenden Schwellen).

### `config_flow.py`
- Die zwei neuen Optionen in den Advanced-Settings-Flow aufnehmen (analog zu bestehenden Schwellen). Nur sichtbar/relevant, wenn `anti_wrinkle_enabled`.

### Tests (`tests/`)
- **Trockner-Regression (Fixture = gestriger Lauf):** ungematchter Zyklus mit Heiz-Vollast + langem ~170-W-Blip-Tail + `anti_wrinkle_enabled=True` → endet als `termination_reason="timeout"` in `STATE_ANTI_WRINKLE`, **nicht** `force_stopped`; aufgezeichnete Dauer nahe echtem Ende (nicht 240 min).
- **WM Soak mittendrin (vor Endschleudern):** Niedriglast-Phase ohne vorheriges Schleudern → **kein** verfrühter Finish (bleibt/kehrt zu RUNNING).
- **WM echtes Ende nach Schleudern:** Schleuder-Phase → Anti-Crease-Blips → früher, sauberer Finish + `STATE_ANTI_WRINKLE`.
- **Opt-out:** `anti_wrinkle_enabled=False` → Blip-Toleranz inaktiv, altes Verhalten (Reset bei jedem Blip).
- **Matched-Regression:** Eco (Spülmaschine) + AutomatikPlus (WM) mit gültigem Profil → Smart-/Timeout-Pfad **unverändert**.
- **Anti-Ghost:** kurzer Niedriglast-Impuls ohne reales Programm (`_cycle_max_power` niedrig) → kein Finish über den neuen Pfad.

### Doku
- README: kurzer Hinweis, dass ungematchte Knitterschutz-Läufe (v. a. Trockner) jetzt sauber via Timeout enden statt force_stop.
- CHANGELOG: Feature-Eintrag im Upstream-Stil (nächste Fork-Version).

## Fehlerfälle / Randbedingungen

- **Opt-in bleibt:** Ohne `anti_wrinkle_enabled` ändert sich nichts (Gate #1).
- **Matched-Pfade unangetastet:** Gate #3 (`_expected_duration <= 0`) schließt jeden gematchten Zyklus aus.
- **Kein neuer State, keine geänderten Default-Schwellen** der bestehenden Erkennung; nur die zwei neuen, device-getunten Parameter.
- **Dishwasher:** hat i. d. R. keinen `anti_wrinkle_enabled`-Knitterschutz → Gate #1 greift nicht; die vorhandene Dishwasher-Sonderlogik (passive drying, Z.~940 ff.) bleibt unberührt.
- **WM-Fehlfrüh-Risiko:** durch die Schleuder-Sicherung + langes `unmatched_off_delay` konservativ abgesichert; „Trockner zuerst" ist das primäre Ziel.

## Offene Punkte / Follow-ups

- **Parameter-Defaults kalibrieren:** `crease_resume_threshold` / `unmatched_off_delay` an realen Fixtures (gestriger Trockner-Lauf; künftige WM-Läufe) feinjustieren.
- **Live-Verifikation:** nächster unbeaufsichtigter Trockner-Lauf auf Tiny → endet er sauber `timeout`/`anti_wrinkle` statt `force_stop`, kommt die Meldung nahe am echten Ende?
- **WM-Pfad** optional erst nach bestandenem Trockner-Live-Test scharfschalten („Trockner zuerst").
- Windows-Testsuite-Kniff (git-excludetes Root-`conftest.py`) wie in den bestehenden Fork-Features beachten.
