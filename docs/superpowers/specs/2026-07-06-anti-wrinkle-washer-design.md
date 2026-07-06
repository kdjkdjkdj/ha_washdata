# Design: Anti-Wrinkle for Washing Machines

**Datum:** 2026-07-06
**Repo:** Fork `kdjkdjkdj/ha_washdata`, Branch `feature/anti-wrinkle-washer`
**Ziel:** Upstream-PR-tauglich. Spec-Doc ist fork-intern, wird vor dem PR gedroppt.

## Problem

WashDatas Anti-Wrinkle-Feature (STATE_ANTI_WRINKLE) erkennt nach Zyklusende die periodischen Trommel-Bursts eines Knitterschutz-Programms und behandelt den Zyklus als abgeschlossen (feuert `cycle_ended`/Finish-Notification bei Hauptzyklus-Ende), statt die Ende-Erkennung durch jeden Burst zurücksetzen zu lassen. Das Feature ist per Code-Gate auf `dryer`/`washer_dryer` beschränkt. Moderne Waschmaschinen haben denselben Effekt: nach Programmende drehen sie die Trommel periodisch (Knitterschutz, ~20–30 W alle ~40 s), bis die Tür geöffnet wird. Ohne Anti-Wrinkle resettet jeder Burst den Ende-Countdown → die „fertig"-Meldung kommt erst nach dem Türöffnen (real beobachtet Tiny 2026-07-05). Mit Anti-Wrinkle käme sie sofort bei Programmende — „Wäsche kann raus".

## Lösung

Das device_type-Gate um `DEVICE_TYPE_WASHING_MACHINE` erweitern. Keine neue Konfiguration, keine geänderten Defaults, kein device_type-Umbau (der die Phasen-Heuristik/Damping berühren und Entry-Neuanlage erfordern würde — bewusst vermieden). Die UI-Checkbox (`anti_wrinkle_section`) ist bereits unkonditional sichtbar und bleibt unverändert; sie greift für Waschmaschinen erst nach dem Gate-Fix.

### Verworfene Alternative
- **Gate komplett entfernen (alle device_types):** semantisch falsch für Geräte ohne Knitterschutz (Spülmaschine, Airfryer); Upstream würde es ablehnen.

## Änderungen

### `cycle_detector.py` (die einzigen zwei Gate-Stellen)
- **~Z. 626–628** — `anti_wrinkle_active`-Bedingung: `self._config.device_type in (DEVICE_TYPE_DRYER, DEVICE_TYPE_WASHER_DRYER)` → zusätzlich `DEVICE_TYPE_WASHING_MACHINE`.
- **~Z. 1443–1444** — Eintritt in `STATE_ANTI_WRINKLE` bei Zyklusende: dieselbe Tupel-Erweiterung.

Beide Tupel identisch halten. Der Import `DEVICE_TYPE_WASHING_MACHINE` existiert in `cycle_detector.py` bereits (Z. 27).

### `tests/test_issue_68_anti_wrinkle.py`
- **`test_anti_wrinkle_not_for_washing_machine` (Z. 213–242) invertieren** → `test_anti_wrinkle_enabled_washing_machine`: mit `device_type=washing_machine` **und** `anti_wrinkle_enabled=True` muss der abgeschlossene Zyklus in `STATE_ANTI_WRINKLE` gehen (mirror `test_anti_wrinkle_enabled_washer_dryer`).
- **Opt-out-Test ergänzen** `test_anti_wrinkle_disabled_washing_machine_finishes`: `device_type=washing_machine`, `anti_wrinkle_enabled=False` → Zyklus endet in `STATE_FINISHED` (kein Anti-Wrinkle ohne Haken; verhindert Regression, dass das Feature ungewollt greift).

### Doku
- README: Anti-Wrinkle-Abschnitt (falls vorhanden) um Waschmaschine ergänzen, sonst kurzer Hinweis.
- CHANGELOG: Feature-Eintrag im Upstream-Stil (Version 0.4.5.10).

## Fehlerfälle / Randbedingungen

- **Opt-in bleibt:** Ohne gesetzten Haken (Default `anti_wrinkle_enabled=False`) ändert sich nichts — Waschmaschinen enden wie bisher in `FINISHED`.
- **Kein Eingriff in die Hauptzyklus-Erkennung:** `STATE_ANTI_WRINKLE` wird erst nach `completed` (termination_reason `timeout`/`smart`) betreten; die Heiz-/Schleuder-Bursts des laufenden Waschgangs (in `RUNNING`) sind davon unberührt. Das `anti_wrinkle_active`-Gate greift nur, wenn bereits in `STATE_ANTI_WRINKLE`.
- **Dishwasher-Sonderlogik** (passive drying, Z. 940 ff.) ist device_type-getrennt und bleibt unangetastet.
- **Effekt-Validierung:** Ob das reale WM-Burst-Muster sauber als Anti-Wrinkle erkannt wird (nicht als Wiederanlauf), zeigt der Live-Test auf Tiny. Defaults (max_power 400 W, max_duration 60 s, exit_power 0,8 W) decken 20–30-W-Bursts klar ab; Tuning bleibt pro Gerät im UI.

## Tests

- Invertierter Positivtest (WM + enabled → ANTI_WRINKLE).
- Neuer Opt-out-Test (WM + disabled → FINISHED).
- Bestehende Dryer/Washer-Dryer-Tests bleiben grün (Regression).

## Deployment & Verifikation

1. Branch `feature/anti-wrinkle-washer`; Commits mit Co-Author-Trailer.
2. Grüne Suite → Merge auf Fork-`main`, `manifest.json` auf 0.4.5.10, Release-Tag `v0.4.5.10` (> 0.4.5.9).
3. HACS-Update auf HA-Tiny; HA-Neustart.
4. User setzt in WashData → Waschmaschine → Settings → Advanced → Anti-Wrinkle Shield den Haken.
5. Nächster Waschlauf mit Knitterschutz: prüfen, dass der Zustand nach Programmende auf „Anti-Wrinkle" springt und die Finish-Notification bei Programmende kommt (nicht erst nach Türöffnen).
6. Danach ggf. Upstream-PR (#2) — Text vorher im Chat freigeben.
