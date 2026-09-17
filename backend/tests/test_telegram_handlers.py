"""Tests de `handlers.py`: parsing de canales, descarga de media y
handlers de eventos (NewMessage / Album) con Telethon mockeado."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto

from app.services.telegram import handlers


def _msg(
    id: int = 1,
    text: str = "hola",
    media=None,
    grouped_id=None,
):
    """Mensaje fake: `fetch_message_content` solo lee .text/.media/
    .chat_id/.id, y el `.text` de un Message TL real necesita cliente."""
    return SimpleNamespace(
        id=id,
        text=text,
        media=media,
        chat_id=-100100,
        grouped_id=grouped_id,
        date=datetime.now(timezone.utc),
    )


class TestParseTargetChannels:
    def test_lista_separada_por_comas(self):
        assert handlers.parse_target_channels("a, b ,c") == ["a", "b", "c"]

    def test_vacia(self):
        assert handlers.parse_target_channels("") == []
        assert handlers.parse_target_channels("  , ") == []


class TestChatIds:
    def test_looks_like_id(self):
        assert handlers.looks_like_id("123")
        assert handlers.looks_like_id("-123")
        assert not handlers.looks_like_id("canal")
        assert not handlers.looks_like_id("-canal")

    def test_to_telegram_chat_id(self):
        assert handlers.to_telegram_chat_id("1914772235") == 1914772235
        assert handlers.to_telegram_chat_id("-1914772235") == 1914772235
        # Formato completo de canal: se conserva el -100...
        assert handlers.to_telegram_chat_id("-1001914772235") == -1001914772235


class TestFetchMessageContent:
    async def test_mensaje_solo_texto(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            handlers,
            "get_settings",
            lambda: SimpleNamespace(
                telegram_media_path=str(tmp_path), openai_api_key="k"
            ),
        )
        client = SimpleNamespace(download_media=AsyncMock())
        text, media_path, ocr = await handlers.fetch_message_content(
            client, _msg(text="hola")
        )
        assert (text, media_path, ocr) == ("hola", None, None)
        client.download_media.assert_not_awaited()

    async def test_foto_con_texto_no_hace_ocr(self, monkeypatch, tmp_path):
        """Un boleto con caption propio no necesita OCR."""
        monkeypatch.setattr(
            handlers,
            "get_settings",
            lambda: SimpleNamespace(
                telegram_media_path=str(tmp_path), openai_api_key="k"
            ),
        )
        ocr_mock = AsyncMock(return_value="OCR")
        monkeypatch.setattr(handlers, "extract_text_from_image", ocr_mock)
        client = SimpleNamespace(download_media=AsyncMock())

        text, media_path, ocr = await handlers.fetch_message_content(
            client, _msg(text="STAKE 2", media=MessageMediaPhoto())
        )
        assert text == "STAKE 2"
        assert media_path is not None and media_path.endswith(".jpg")
        assert ocr is None
        ocr_mock.assert_not_awaited()

    async def test_foto_sin_texto_hace_ocr(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            handlers,
            "get_settings",
            lambda: SimpleNamespace(
                telegram_media_path=str(tmp_path), openai_api_key="k"
            ),
        )
        ocr_mock = AsyncMock(return_value="texto ocr")
        monkeypatch.setattr(handlers, "extract_text_from_image", ocr_mock)
        client = SimpleNamespace(download_media=AsyncMock())

        text, media_path, ocr = await handlers.fetch_message_content(
            client, _msg(text="", media=MessageMediaPhoto())
        )
        assert text == ""
        assert media_path is not None
        assert ocr == "texto ocr"
        ocr_mock.assert_awaited_once()

    async def test_descarga_fallida_no_aborta(self, monkeypatch, tmp_path):
        """Si download_media lanza, el mensaje sigue sin media (raw vacío
        que el catch-up reintentará)."""
        monkeypatch.setattr(
            handlers,
            "get_settings",
            lambda: SimpleNamespace(
                telegram_media_path=str(tmp_path), openai_api_key="k"
            ),
        )
        client = SimpleNamespace(
            download_media=AsyncMock(side_effect=Exception("fallo red"))
        )
        text, media_path, ocr = await handlers.fetch_message_content(
            client, _msg(text="hola", media=MessageMediaPhoto())
        )
        assert (text, media_path, ocr) == ("hola", None, None)

    async def test_media_no_foto_se_ignora(self, monkeypatch, tmp_path):
        """Documentos/vídeos no pasan por OCR ni descarga."""
        monkeypatch.setattr(
            handlers,
            "get_settings",
            lambda: SimpleNamespace(
                telegram_media_path=str(tmp_path), openai_api_key="k"
            ),
        )
        client = SimpleNamespace(download_media=AsyncMock())
        text, media_path, ocr = await handlers.fetch_message_content(
            client, _msg(text="hola", media=MessageMediaDocument())
        )
        assert (text, media_path, ocr) == ("hola", None, None)
        client.download_media.assert_not_awaited()


class TestNewMessageHandler:
    async def test_miembro_de_album_se_ignora(self, monkeypatch):
        """Los mensajes con grouped_id los procesa el handler de Album."""
        proc = AsyncMock()
        monkeypatch.setattr(handlers, "process_incoming_message", proc)
        handler = handlers._make_new_message_handler("canal")

        event = SimpleNamespace(
            message=_msg(grouped_id=999),
            client=SimpleNamespace(),
            chat_id=-100100,
            get_chat=AsyncMock(return_value=SimpleNamespace(title="Canal")),
        )
        await handler(event)
        proc.assert_not_awaited()

    async def test_mensaje_normal_se_procesa(self, monkeypatch):
        proc = AsyncMock()
        fetch = AsyncMock(return_value=("texto pick", None, None))
        monkeypatch.setattr(handlers, "process_incoming_message", proc)
        monkeypatch.setattr(handlers, "fetch_message_content", fetch)
        handler = handlers._make_new_message_handler("canal")

        msg = _msg(id=42, text="texto pick")
        event = SimpleNamespace(
            message=msg,
            client=SimpleNamespace(),
            chat_id=-100100,
            get_chat=AsyncMock(return_value=SimpleNamespace(title="Canal")),
        )
        await handler(event)

        proc.assert_awaited_once()
        kwargs = proc.await_args.kwargs
        assert kwargs["channel"] == "Canal"
        assert kwargs["channel_id"] == -100100
        assert kwargs["message_id"] == 42
        assert kwargs["text"] == "texto pick"
        assert kwargs["message_date"] == msg.date


class TestAlbumHandler:
    async def test_cada_foto_es_un_pick_y_hereda_caption(self, monkeypatch):
        proc = AsyncMock()
        # Las fotos del álbum no llevan texto propio; fetch devuelve "".
        fetch = AsyncMock(return_value=("", "/media/f.jpg", "ocr"))
        monkeypatch.setattr(handlers, "process_incoming_message", proc)
        monkeypatch.setattr(handlers, "fetch_message_content", fetch)
        handler = handlers._make_album_handler("canal")

        m1 = _msg(id=1, text="STAKE 4 del pack", media=MessageMediaPhoto())
        m2 = _msg(id=2, text="", media=MessageMediaPhoto())
        event = SimpleNamespace(
            messages=[m1, m2],
            client=SimpleNamespace(),
            chat_id=-100100,
            get_chat=AsyncMock(return_value=SimpleNamespace(title="Canal")),
        )
        await handler(event)

        assert proc.await_count == 2
        calls = [c.kwargs for c in proc.await_args_list]
        # El primero conserva su caption; el segundo hereda la compartida.
        assert calls[0]["text"] == "STAKE 4 del pack"
        assert calls[1]["text"] == "STAKE 4 del pack"
        assert {c["message_id"] for c in calls} == {1, 2}
