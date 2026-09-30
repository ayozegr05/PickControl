"""Tests de `app/services/results/base.py` — `provider_state.json`.

Cubre la fusión en `_save_state`: el backend mantiene `_STATE` en
memoria y procesos paralelos (backfill manual, scripts de limpieza)
escriben el mismo archivo — un dump ciego les borraría sus marcas.
"""

import json
from datetime import UTC, datetime

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


def test_save_state_prefiere_marca_mayor_en_conflicto():
    """En conflicto por la misma clave gana el valor mayor: conserva
    el cooldown/miss más largo y el contador de llamadas más alto."""
    today = datetime.now(UTC).date().isoformat()
    state_file = results_base._STATE_FILE

    results_base.count_provider_call("espn")

    disk = json.loads(state_file.read_text(encoding="utf-8"))
    disk["calls"][today]["espn"] = 99
    disk["loops"]["verifier"] = "2099-12-31T00:00:00"
    state_file.write_text(json.dumps(disk), encoding="utf-8")

    results_base.count_provider_call("espn")

    final = json.loads(state_file.read_text(encoding="utf-8"))
    assert final["calls"][today]["espn"] == 99
    assert final["loops"]["verifier"] == "2099-12-31T00:00:00"
