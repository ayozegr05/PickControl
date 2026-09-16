"""Tests de integración de los endpoints de informantes (`app/api/v1/informantes.py`)."""

from httpx import AsyncClient

PICK_CREATE = {
    "apuesta": "Real Madrid gana",
    "informante": "TipsterPro",
    "tipo_de_apuesta": "1x2",
    "casa": "Bwin",
    "cantidad_apostada": 20.0,
    "cuota": 1.8,
}


class TestInformantes:
    async def test_informante_inexistente(self, client: AsyncClient):
        response = await client.get("/api/v1/informante/NoExiste")
        assert response.status_code == 404

    async def test_informante_existe_pero_sin_picks(
        self, client: AsyncClient, auth_headers, crear_canal
    ):
        await crear_canal(PICK_CREATE["informante"])
        # Crear una apuesta con un informante, luego borrarla.
        create = await client.post(
            "/api/v1/apuestas", json=PICK_CREATE, headers=auth_headers
        )
        pick_id = create.json()["id"]
        await client.delete(f"/api/v1/apuestas/{pick_id}", headers=auth_headers)

        # El informante sigue en la BD, pero sin picks -> 404 según la lógica actual.
        response = await client.get("/api/v1/informante/TipsterPro")
        assert response.status_code == 404

    async def test_stats_informante_con_mixta(
        self, client: AsyncClient, auth_headers, crear_canal
    ):
        await crear_canal(PICK_CREATE["informante"])
        # Crear dos apuestas del mismo informante: una acierto y otra fallo.
        acierto = PICK_CREATE.copy()
        acierto["cuota"] = 2.0
        create_a = await client.post(
            "/api/v1/apuestas", json=acierto, headers=auth_headers
        )
        pick_a_id = create_a.json()["id"]
        await client.put(
            f"/api/v1/apuesta/{pick_a_id}",
            json={"acierto": "True"},
            headers=auth_headers,
        )

        fallo = PICK_CREATE.copy()
        fallo["cuota"] = 2.0
        create_f = await client.post(
            "/api/v1/apuestas", json=fallo, headers=auth_headers
        )
        pick_f_id = create_f.json()["id"]
        await client.put(
            f"/api/v1/apuesta/{pick_f_id}",
            json={"acierto": "False"},
            headers=auth_headers,
        )

        response = await client.get("/api/v1/informante/TipsterPro")
        assert response.status_code == 200
        data = response.json()
        assert data["informante"] == "TipsterPro"
        assert data["total_apuestas"] == 2
        assert data["total_aciertos"] == 1
        assert data["ganancias"] == 0.0  # +10 - 10
        assert data["porcentaje_aciertos"] == 50.0
        assert data["yield_pct"] == 0.0  # ganancias 0 / total apostado 40
        assert len(data["apuestas"]) == 2

    async def test_listar_informantes_solo_canales_telegram(
        self, client: AsyncClient, session, crear_canal
    ):
        """GET /informantes solo lista canales de Telegram reales; los
        informantes sin canal (es_canal_telegram=False) quedan fuera."""
        from app.models.informante import Informante

        await crear_canal("CanalReal")
        session.add(Informante(nombre="ManualInventado", es_canal_telegram=False))
        await session.commit()

        response = await client.get("/api/v1/informantes")
        assert response.status_code == 200
        nombres = [i["informante"] for i in response.json()]
        assert "CanalReal" in nombres
        assert "ManualInventado" not in nombres
