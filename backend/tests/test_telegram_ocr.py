"""Tests de `ocr.py`: extracción de texto de imágenes con OpenAI.

El cliente de OpenAI se mockeado (`openai.AsyncOpenAI` se importa dentro
de la función, así que basta parchear el atributo del módulo).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services.telegram import ocr


def _fake_openai(content: str = "TEXTO EXTRAIDO", error: Exception | None = None):
    create = AsyncMock()
    if error is not None:
        create.side_effect = error
    else:
        create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )
    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )


class TestExtractTextFromImage:
    async def test_sin_api_key_devuelve_none(self):
        assert await ocr.extract_text_from_image("x.jpg", None) is None
        assert await ocr.extract_text_from_image("x.jpg", "") is None

    async def test_archivo_inexistente_devuelve_none(self, tmp_path):
        missing = tmp_path / "no_existe.jpg"
        assert await ocr.extract_text_from_image(str(missing), "key") is None

    async def test_ocr_ok(self, monkeypatch, tmp_path):
        img = tmp_path / "boleto.jpg"
        img.write_bytes(b"\xff\xd8\xff")  # cabecera JPEG cualquiera

        monkeypatch.setattr("openai.AsyncOpenAI", lambda **kw: _fake_openai("STAKE 2"))
        result = await ocr.extract_text_from_image(str(img), "key")
        assert result == "STAKE 2"

    async def test_error_openai_devuelve_none(self, monkeypatch, tmp_path):
        """Un fallo de la API no tumba el pipeline: raw queda sin OCR y
        el catch-up lo reintentará."""
        img = tmp_path / "boleto.jpg"
        img.write_bytes(b"\xff\xd8\xff")

        monkeypatch.setattr(
            "openai.AsyncOpenAI",
            lambda **kw: _fake_openai(error=Exception("api caida")),
        )
        assert await ocr.extract_text_from_image(str(img), "key") is None
