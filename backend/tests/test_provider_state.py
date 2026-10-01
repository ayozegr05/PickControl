"""Tests de `app/services/results/base.py` — `provider_state.json`.

Cubre la fusión en `_save_state`: el backend mantiene `_STATE` en
memoria y procesos paralelos (backfill manual, scripts de limpieza)
escriben el mismo archivo — un dump ciego les borraría sus marcas.
"""

import json
from datetime import UTC, datetime, timedelta

import app.services.results.base as results_base


def test_save_state_fusiona_escrituras_de_otro_proceso():
    """Si otro proceso escribe `provider_state.json` mientras el backend
    tiene `_STATE` en memoria, el siguiente guardado conserva ambas
    marcas (calls por día y misses) en vez de pisar el archivo."""
    today = datetime.now(UTC).date().isoformat()
    state_file = results_base._STATE_FILE

    # El backend marca una llamada (carga _STATE en memoria y guarda).
    results_base.count_provider_call("espn")

    # Otro proceso escribe sus propias marcas sobre el archivo.
    disk = json.loads(state_file.read_text(encoding="utf-8"))
    disk["calls"][today]["gemini-odds"] = 18
    disk["missed"]["gemini-odds|postponed|42"] = "2099-01-01T00:00:00"
    state_file.write_text(json.dumps(disk), encoding="utf-8")

    # El backend vuelve a guardar: no debe pisar lo del otro proceso.
    results_base.count_provider_call("espn")

    final = json.loads(state_file.read_text(encoding="utf-8"))
    assert final["calls"][today]["espn"] == 2
    assert final["calls"][today]["gemini-odds"] == 18
    assert final["missed"]["gemini-odds|postponed|42"] == "2099-01-01T00:00:00"


def test_save_state_preserva_claves_ajenas_y_memoria_gana_conflicto():
    """Claves que solo existen en disco se conservan (loops de otro
    proceso, contadores mayores); pero si la MISMA clave está en ambos
    lados gana la memoria — la escritura más reciente. Un max() ciego
    resucitaría bloqueos que este proceso acaba de acortar."""
    today = datetime.now(UTC).date().isoformat()
    state_file = results_base._STATE_FILE

    results_base.count_provider_call("espn")

    disk = json.loads(state_file.read_text(encoding="utf-8"))
    disk["calls"][today]["espn"] = 99
    disk["loops"]["verifier"] = "2099-12-31T00:00:00"
    state_file.write_text(json.dumps(disk), encoding="utf-8")

    # Este proceso acorta un bloqueo: la copia vieja en disco (años)
    # no debe resucitar.
    results_base.mark_cooldown("prov_x", datetime.now(UTC) + timedelta(hours=1))
    disk2 = json.loads(state_file.read_text(encoding="utf-8"))
    disk2["rate_limited"]["prov_x"] = "2099-12-31T00:00:00"
    state_file.write_text(json.dumps(disk2), encoding="utf-8")

    results_base.count_provider_call("oddsfeed")

    final = json.loads(state_file.read_text(encoding="utf-8"))
    # El contador ajeno se preserva (max por día/provider).
    assert final["calls"][today]["espn"] == 99
    assert final["calls"][today]["oddsfeed"] == 1
    assert final["loops"]["verifier"] == "2099-12-31T00:00:00"
    # La versión corta en memoria gana a la larga de disco.
    assert final["rate_limited"]["prov_x"] < "2099-12-31T00:00:00"


def test_record_loop_summary_acumula_historial():
    """Cada pasada se añade al historial (tope _LOOP_HISTORY_MAX) y la
    última sigue disponible como resumen suelto."""
    for i in range(12):
        results_base.record_loop_summary("odds_backfill", {"filas": i})

    assert results_base.loop_summary("odds_backfill")["filas"] == 11
    history = results_base.loop_history("odds_backfill")
    assert len(history) == results_base._LOOP_HISTORY_MAX
    # Más reciente al final; se conservan las últimas 10 (2..11).
    assert history[-1]["filas"] == 11
    assert history[0]["filas"] == 2


def test_odds_rows_by_day_acumula_y_poda():
    """El contador diario suma por día UTC y se poda a 14 días."""
    results_base.count_odds_rows_written(5)
    results_base.count_odds_rows_written(7)
    today = datetime.now(UTC).date().isoformat()

    by_day = results_base.odds_rows_by_day()
    assert by_day[today] == 12
    # n<=0 no registra nada.
    results_base.count_odds_rows_written(0)
    assert len(by_day) == len(results_base.odds_rows_by_day())
