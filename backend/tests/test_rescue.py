"""Tests del rescate de mantenimiento (`services/maintenance/rescue.py`).

`AsyncSessionLocal` se sobreescribe con la sesión SQLite del fixture —
mismo patrón que `test_notifications.py`. OpenAI se corta en
`extract_text_from_image` / `extract_pick` y `get_settings` se
sustituye por un stub con `openai_api_key` presente.
"""

from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlmodel import select

from app.core.dates import utc_now
from app.models.odds_snapshot import OddsSnapshot
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.services.maintenance import rescue
from app.services.odds.historical_backfill import count_pending_backfill
from app.services.telegram.pick_extractor import ExtractedPick

pytestmark = pytest.mark.asyncio


def _session_cm(session):
    """`AsyncSessionLocal` falso que devuelve la sesión del test."""

    @asynccontextmanager
    async def _cm():
        yield session

    return _cm


def _settings_stub(monkeypatch, max_age_days: float = 30.0):
    monkeypatch.setattr(
        rescue,
        "get_settings",
        lambda: SimpleNamespace(
            openai_api_key="test-key", rescue_max_age_days=max_age_days
        ),
    )


_MSG_ID = [0]


async def _raw(session, **kwargs) -> TelegramRawMessage:
    kwargs.setdefault("channel_id", 1)
    kwargs.setdefault("channel_name", "Canal Test")
    kwargs.setdefault("text", "")
    kwargs.setdefault("received_at", utc_now())
    _MSG_ID[0] += 1
    kwargs.setdefault("message_id", _MSG_ID[0])
    raw = TelegramRawMessage(**kwargs)
    session.add(raw)
    await session.commit()
    await session.refresh(raw)
    return raw


class TestRetryPendingOcr:
    async def test_ocr_ok_marca_reproceso(self, session, monkeypatch, tmp_path):
        media = tmp_path / "foto.jpg"
        media.write_bytes(b"fake")
        raw = await _raw(session, media_path=str(media), processed=True)
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))
        monkeypatch.setattr(
            rescue,
            "extract_text_from_image",
            AsyncMock(return_value="SLIP OCR"),
        )

        resumen = await rescue.retry_pending_ocr()

        assert resumen == {
            "pendientes": 1,
            "sin_fichero": 0,
            "hechos": 1,
            "fallos": 0,
        }
        await session.refresh(raw)
        assert raw.extracted_text == "SLIP OCR"
        # Sin pick asociado -> queda para el reproceso.
        assert raw.processed is False

    async def test_fichero_ausente_se_salta_sin_error(self, session, monkeypatch):
        raw = await _raw(session, media_path="media/telegram/no_existe_xyz.jpg")
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))
        ocr = AsyncMock(return_value="X")
        monkeypatch.setattr(rescue, "extract_text_from_image", ocr)

        resumen = await rescue.retry_pending_ocr()

        # No está en pendientes (sin fichero) ni falla: simplemente se
        # salta — si el fichero reaparece, una pasada futura lo procesa.
        assert resumen["pendientes"] == 0
        ocr.assert_not_called()
        await session.refresh(raw)
        assert raw.extracted_text is None

    async def test_ocr_vacio_cuenta_como_fallo(self, session, monkeypatch, tmp_path):
        media = tmp_path / "foto.jpg"
        media.write_bytes(b"fake")
        await _raw(session, media_path=str(media))
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))
        monkeypatch.setattr(
            rescue, "extract_text_from_image", AsyncMock(return_value="  ")
        )

        resumen = await rescue.retry_pending_ocr()
        assert resumen["fallos"] == 1

    async def test_foto_antigua_queda_fuera_de_ventana(
        self, session, monkeypatch, tmp_path
    ):
        media = tmp_path / "foto_vieja.jpg"
        media.write_bytes(b"fake")
        raw = await _raw(
            session,
            media_path=str(media),
            received_at=utc_now() - timedelta(days=60),
        )
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))
        ocr = AsyncMock(return_value="X")
        monkeypatch.setattr(rescue, "extract_text_from_image", ocr)

        resumen = await rescue.retry_pending_ocr()

        assert resumen["pendientes"] == 0
        ocr.assert_not_called()
        await session.refresh(raw)
        assert raw.extracted_text is None

        # Con la ventana desactivada (--max-age-days 0) sí se reintenta.
        resumen = await rescue.retry_pending_ocr(max_age_days=0)
        assert resumen["hechos"] == 1
        await session.refresh(raw)
        assert raw.extracted_text == "X"


class TestReprocessPendingRaws:
    async def test_raw_pendiente_crea_pick(self, session, monkeypatch):
        raw = await _raw(
            session,
            text="Real Madrid gana cuota 2.0",
            processed=False,
        )
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))
        monkeypatch.setattr(
            rescue,
            "extract_pick",
            AsyncMock(
                return_value=ExtractedPick(
                    es_apuesta=True,
                    seleccion="Real Madrid gana",
                    cuota=2.0,
                    metodo="rule",
                )
            ),
        )
        monkeypatch.setattr(
            rescue, "_find_duplicate_pick", AsyncMock(return_value=None)
        )

        resumen = await rescue.reprocess_pending_raws()

        assert resumen["procesados"] == 1
        assert resumen["picks"] == 1
        await session.refresh(raw)
        assert raw.processed is True
        pick = (
            await session.exec(
                select(ParsedPick).where(ParsedPick.raw_message_id == raw.id)
            )
        ).first()
        assert pick is not None
        assert pick.seleccion == "Real Madrid gana"
        # Sin fecha en el texto: se usa received_at.
        assert pick.fecha_evento == raw.received_at

    async def test_sin_texto_queda_pendiente(self, session, monkeypatch):
        raw = await _raw(session, text="", processed=False, media_path="media/x.jpg")
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))
        extract = AsyncMock()
        monkeypatch.setattr(rescue, "extract_pick", extract)

        resumen = await rescue.reprocess_pending_raws()

        # Con media referenciada sigue pendiente: el fichero puede
        # reaparecer y el OCR lo rescataría.
        assert resumen["procesados"] == 0
        extract.assert_not_called()
        await session.refresh(raw)
        assert raw.processed is False

    async def test_vacio_sin_media_se_cierra_ya(self, session, monkeypatch):
        """Sin texto y sin media referenciada no hay nada que rescatar:
        se cierra de inmediato sin esperar a la ventana de antigüedad."""
        raw = await _raw(session, text="", processed=False)
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))
        extract = AsyncMock()
        monkeypatch.setattr(rescue, "extract_pick", extract)

        resumen = await rescue.reprocess_pending_raws()

        assert resumen["sin_pick"] == 1
        extract.assert_not_called()
        await session.refresh(raw)
        assert raw.processed is True

    async def test_no_es_pick_cierra_raw(self, session, monkeypatch):
        """Si el extractor dice "no es pick" el raw se cierra — si no se
        reenviaría al LLM en cada ciclo para siempre."""
        raw = await _raw(session, text="mensaje de chat sin pick", processed=False)
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))
        monkeypatch.setattr(rescue, "extract_pick", AsyncMock(return_value=None))

        resumen = await rescue.reprocess_pending_raws()

        assert resumen["sin_pick"] == 1
        assert resumen["procesados"] == 0
        await session.refresh(raw)
        assert raw.processed is True

    async def test_raw_antiguo_se_descarta_sin_llm(self, session, monkeypatch):
        """Fuera de la ventana de rescate: processed=True sin llamar al
        LLM — la fila queda en BD pero deja de acumularse en la cola."""
        raw = await _raw(
            session,
            text="pick viejo",
            processed=False,
            received_at=utc_now() - timedelta(days=60),
        )
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))
        extract = AsyncMock()
        monkeypatch.setattr(rescue, "extract_pick", extract)

        resumen = await rescue.reprocess_pending_raws()

        assert resumen["antiguos"] == 1
        assert resumen["pendientes"] == 0
        extract.assert_not_called()
        await session.refresh(raw)
        assert raw.processed is True

        # El descarte es permanente: ni sin ventana vuelve a intentarse.
        resumen = await rescue.reprocess_pending_raws(max_age_days=0)
        assert resumen["pendientes"] == 0
        extract.assert_not_called()


class TestRetryIncompletePicks:
    """`retry_incomplete_picks`: 2ª pasada LLM sobre picks `rule` sin
    `evento`. El LLM manda en los campos que aporta; un fallo no toca
    nada y un "no es apuesta" analizado descarta la fila."""

    async def _pick_sin_evento(self, session, raw, **kwargs) -> ParsedPick:
        pick = ParsedPick(
            raw_message_id=raw.id,
            es_apuesta=True,
            seleccion="CHALL GÉNOVA",
            deporte="tenis",
            metodo="rule",
            evento=None,
            **kwargs,
        )
        session.add(pick)
        await session.commit()
        await session.refresh(pick)
        return pick

    async def test_llm_rellena_evento_y_campos(self, session, monkeypatch):
        raw = await _raw(session, text="Neumayer gana cuota 1.5", processed=True)
        pick = await self._pick_sin_evento(session, raw)
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))
        monkeypatch.setattr(
            rescue,
            "_llm_extract",
            AsyncMock(
                return_value=ExtractedPick(
                    es_apuesta=True,
                    evento="Neumayer vs Manzano",
                    seleccion="Neumayer gana",
                    deporte="tenis",
                    mercado="ganador",
                    cuota=1.5,
                )
            ),
        )

        resumen = await rescue.retry_incomplete_picks()

        assert resumen == {
            "candidatos": 1,
            "rellenados": 1,
            "descartados": 0,
            "fallos": 0,
        }
        await session.refresh(pick)
        assert pick.evento == "Neumayer vs Manzano"
        assert pick.seleccion == "Neumayer gana"
        assert pick.cuota == 1.5
        assert pick.metodo == "rule+llm"

    async def test_llm_dice_no_apuesta_descarta(self, session, monkeypatch):
        raw = await _raw(session, text="marketing del canal", processed=True)
        pick = await self._pick_sin_evento(session, raw)
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))
        monkeypatch.setattr(
            rescue,
            "_llm_extract",
            AsyncMock(return_value=ExtractedPick(es_apuesta=False)),
        )

        resumen = await rescue.retry_incomplete_picks()

        assert resumen["descartados"] == 1
        await session.refresh(pick)
        assert pick.es_apuesta is False

    async def test_llm_falla_no_toca_nada(self, session, monkeypatch):
        raw = await _raw(session, text="Sakkari gana", processed=True)
        pick = await self._pick_sin_evento(session, raw)
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))
        monkeypatch.setattr(
            rescue, "_llm_extract", AsyncMock(side_effect=Exception("boom"))
        )

        resumen = await rescue.retry_incomplete_picks()

        assert resumen["fallos"] == 1
        await session.refresh(pick)
        assert pick.evento is None
        assert pick.es_apuesta is True

    async def test_pick_antiguo_queda_fuera(self, session, monkeypatch):
        raw = await _raw(
            session,
            text="pick viejo",
            processed=True,
            received_at=utc_now() - timedelta(days=60),
        )
        await self._pick_sin_evento(session, raw)
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))
        llm = AsyncMock()
        monkeypatch.setattr(rescue, "_llm_extract", llm)

        resumen = await rescue.retry_incomplete_picks()

        assert resumen["candidatos"] == 0
        llm.assert_not_called()

    async def test_padre_de_combinada_no_entra(self, session, monkeypatch):
        raw = await _raw(session, text="combinada", processed=True)
        await self._pick_sin_evento(session, raw, es_combinada=True)
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))
        llm = AsyncMock()
        monkeypatch.setattr(rescue, "_llm_extract", llm)

        resumen = await rescue.retry_incomplete_picks()

        assert resumen["candidatos"] == 0
        llm.assert_not_called()

    async def test_evento_basura_tambien_es_candidato(self, session, monkeypatch):
        """No solo el vacío: "Lun - sep" (encabezado de día extraído por
        error) también entra a la 2ª pasada LLM."""
        raw = await _raw(session, text="Suecia Polonia tarjetas", processed=True)
        pick = await self._pick_sin_evento(session, raw)
        pick.evento = "Lun - sep"
        await session.commit()
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))
        monkeypatch.setattr(
            rescue,
            "_llm_extract",
            AsyncMock(
                return_value=ExtractedPick(
                    es_apuesta=True,
                    evento="Suecia vs Polonia",
                    deporte="fútbol",
                )
            ),
        )

        resumen = await rescue.retry_incomplete_picks()

        assert resumen["rellenados"] == 1
        await session.refresh(pick)
        assert pick.evento == "Suecia vs Polonia"

    async def test_pata_solo_rellena_evento_y_deporte(self, session, monkeypatch):
        """Las patas entran al rescate (comparten el fixture del boleto)
        pero solo se les escriben `evento`/`deporte`: el LLM devuelve el
        boleto completo y mercado/cuota/línea son propios de cada pata."""
        raw = await _raw(session, text="slip de dos patas", processed=True)
        padre = await self._pick_sin_evento(session, raw, es_combinada=True)
        padre.acierto = True  # el padre ya liquidado no es candidato
        pata = await self._pick_sin_evento(
            session, raw, combinada_id=padre.id, mercado="over/under juegos"
        )
        await session.commit()
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))
        monkeypatch.setattr(
            rescue,
            "_llm_extract",
            AsyncMock(
                return_value=ExtractedPick(
                    es_apuesta=True,
                    evento="Rottgering vs Winter",
                    deporte="tenis",
                    mercado="ganador",
                    cuota=9.9,
                )
            ),
        )

        resumen = await rescue.retry_incomplete_picks()

        assert resumen["rellenados"] == 1
        await session.refresh(pata)
        assert pata.evento == "Rottgering vs Winter"
        assert pata.deporte == "tenis"
        # El resto de la pata no se toca: ni mercado ni cuota ni metodo.
        assert pata.mercado == "over/under juegos"
        assert pata.cuota is None
        assert pata.metodo == "rule"

    async def test_pata_no_se_descarta_aunque_llm_diga_no_apuesta(
        self, session, monkeypatch
    ):
        """Un `es_apuesta=False` del LLM no puede marcar una pata:
        rompería la liquidación del padre."""
        raw = await _raw(session, text="slip", processed=True)
        padre = await self._pick_sin_evento(session, raw, es_combinada=True)
        padre.acierto = True
        pata = await self._pick_sin_evento(session, raw, combinada_id=padre.id)
        await session.commit()
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))
        monkeypatch.setattr(
            rescue,
            "_llm_extract",
            AsyncMock(return_value=ExtractedPick(es_apuesta=False)),
        )

        resumen = await rescue.retry_incomplete_picks()

        assert resumen["descartados"] == 0
        await session.refresh(pata)
        assert pata.es_apuesta is True
        assert pata.evento is None


class TestBackfillPendingCount:
    async def test_cuenta_solo_pendientes_reales(self, session, monkeypatch):
        # Pick elegible sin backfill.
        await self._pick(session, odds_event_id=None)
        # Pick ya procesado (evento con filas oddspapi).
        done_pick = await self._pick(session, odds_event_id="oddspapi:99")
        session.add(
            OddsSnapshot(
                provider="oddspapi",
                event_ext_id="oddspapi:99",
                market_name="Full time",
                choice_name="1",
                cuota=1.9,
                captured_at=utc_now(),
            )
        )
        # Deporte no soportado por NINGUNA fuente de backfill
        # (automovilismo — odds-feed sí cubre baloncesto): no cuenta.
        await self._pick(session, deporte="automovilismo")
        await session.commit()

        assert await count_pending_backfill(session) == 1
        _ = done_pick

    async def test_nofixture_miss_excluye(self, session, monkeypatch, tmp_path):
        from app.services.results import base as results_base

        monkeypatch.setattr(results_base, "_STATE", None)
        monkeypatch.setattr(
            results_base, "_STATE_FILE", tmp_path / "provider_state.json"
        )
        pick = await self._pick(session, odds_event_id=None)
        await session.commit()
        assert await count_pending_backfill(session) == 1

        # Miss de nofixture en TODAS las fuentes (las claves son por
        # proveedor: una fuente puede cubrir lo que la otra no).
        results_base.mark_missed(f"oddspapi|nofixture|{pick.id}")
        results_base.mark_missed(f"oddsfeed|nofixture|{pick.id}")
        assert await count_pending_backfill(session) == 0

    async def _pick(self, session, **kwargs) -> ParsedPick:
        kwargs.setdefault("raw_message_id", 1)
        kwargs.setdefault("es_apuesta", True)
        kwargs.setdefault("es_combinada", False)
        kwargs.setdefault("deporte", "futbol")
        kwargs.setdefault("fecha_evento", utc_now() - timedelta(days=1))
        pick = ParsedPick(seleccion="X gana", **kwargs)
        session.add(pick)
        await session.flush()
        return pick


_SETTLED_SLIP = (
    "SELLO: GANADOR\n"
    "Acumulador 5 @ 91.00\n"
    "Ipswich - Liverpool\n"
    "✓ Alexander Isak: Marca gol\n"
    "✓ Julio Enciso: Recibirá una tarjeta\n"
    "Apuesta\n35,00€\nGANASTE\n3.185,00€"
)


class TestRetriagePendingPicks:
    """`retriage_pending_picks`: las reglas deterministas de rechazo se
    aplican sobre pendientes `es_apuesta=True` — un pick que entró
    antes de que existiera la regla (boleto liquidado) se reclasifica
    solo, sin LLM ni cuota."""

    async def _pick(self, session, raw, **kwargs) -> ParsedPick:
        pick = ParsedPick(
            raw_message_id=raw.id,
            es_apuesta=True,
            seleccion="X gana",
            deporte="futbol",
            evento="Ipswich - Liverpool",
            metodo="llm",
            **kwargs,
        )
        session.add(pick)
        await session.commit()
        await session.refresh(pick)
        return pick

    async def test_boleto_liquidado_se_descarta(self, session, monkeypatch):
        raw = await _raw(session, extracted_text=_SETTLED_SLIP, processed=True)
        pick = await self._pick(session, raw)
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))

        resumen = await rescue.retriage_pending_picks()

        assert resumen == {"candidatos": 1, "descartados": 1}
        await session.refresh(pick)
        assert pick.es_apuesta is False

    async def test_pick_normal_no_se_toca(self, session, monkeypatch):
        raw = await _raw(session, text="Munar gana cuota 1.8", processed=True)
        pick = await self._pick(session, raw)
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))

        resumen = await rescue.retriage_pending_picks()

        assert resumen == {"candidatos": 1, "descartados": 0}
        await session.refresh(pick)
        assert pick.es_apuesta is True

    async def test_ya_liquidado_no_es_candidato(self, session, monkeypatch):
        raw = await _raw(session, extracted_text=_SETTLED_SLIP, processed=True)
        pick = await self._pick(session, raw, acierto=True)
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))

        resumen = await rescue.retriage_pending_picks()

        assert resumen["candidatos"] == 0
        await session.refresh(pick)
        assert pick.es_apuesta is True

    async def test_patas_tambien_se_descartan_con_el_raw(self, session, monkeypatch):
        """Padre y patas comparten el raw del slip: si el boleto era un
        repost liquidado, toda la familia queda fuera a la vez."""
        raw = await _raw(session, extracted_text=_SETTLED_SLIP, processed=True)
        padre = await self._pick(session, raw, es_combinada=True)
        pata = await self._pick(session, raw, combinada_id=padre.id)
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))

        resumen = await rescue.retriage_pending_picks()

        assert resumen["descartados"] == 2
        await session.refresh(padre)
        await session.refresh(pata)
        assert padre.es_apuesta is False
        assert pata.es_apuesta is False

    async def test_raw_sin_texto_no_es_descartable(self, session, monkeypatch):
        raw = await _raw(session, text="", processed=True)
        pick = await self._pick(session, raw)
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))

        resumen = await rescue.retriage_pending_picks()

        assert resumen["descartados"] == 0
        await session.refresh(pick)
        assert pick.es_apuesta is True

    async def test_par_slip_liquidado_descarta_el_texto(self, session, monkeypatch):
        """Par foto+texto (caso 5158): el pick nació del mensaje de
        texto pero el slip emparejado en ±10 min era un boleto ya
        cobrado — el material combinado manda y el pick se descarta."""
        now = utc_now()
        slip = await _raw(
            session,
            extracted_text=_SETTLED_SLIP,
            received_at=now - timedelta(minutes=1),
            processed=True,
        )
        raw = await _raw(
            session,
            text="STAKE 3\nNATIONS LEAGUE\nMÁS DE 6.0 CÓRNERS",
            received_at=now,
            processed=True,
        )
        pick = await self._pick(session, raw)
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))

        resumen = await rescue.retriage_pending_picks()

        assert resumen["descartados"] == 1
        await session.refresh(pick)
        assert pick.es_apuesta is False
        _ = slip


class TestRepairSettledSlip:
    """`repair_pick`: el material liquidado se rechaza sin llegar al
    fallback de reglas — si no, "GANADOR" del sello acababa como
    selección rescatada (caso 5158)."""

    async def test_par_liquidado_rechaza_sin_fallback(self, session):
        from app.services.maintenance import repair

        now = utc_now()
        await _raw(
            session,
            extracted_text=_SETTLED_SLIP,
            received_at=now - timedelta(minutes=1),
            processed=True,
        )
        raw = await _raw(
            session,
            text="STAKE 3\nNATIONS LEAGUE\nMÁS DE 6.0 CÓRNERS",
            received_at=now,
            processed=True,
        )
        pick = ParsedPick(
            raw_message_id=raw.id,
            es_apuesta=True,
            seleccion="GANADOR",
            deporte="fútbol",
            evento="Chipre v Armenia",
            metodo="llm",
        )
        session.add(pick)
        await session.commit()
        await session.refresh(pick)

        report = await repair.repair_pick(session, pick, apply=True)

        assert report.action == "rejected"
        await session.refresh(pick)
        assert pick.es_apuesta is False
        assert pick.metodo == "rejected+reext"
