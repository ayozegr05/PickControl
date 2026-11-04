"""Tests de `ocr.py`: contrato de `extract_text_from_image` /
`ocr_image` sobre la cadena gratuita (Gemini → visión compat).

La cadena y los cooldowns por endpoint se prueban a fondo en
`test_ocr_fallback.py`; aquí quedan los casos de borde de la API
pública que usan processor/handlers/event_rescue.
"""

from unittest.mock import AsyncMock

from app.services.telegram import ocr


class TestExtractTextFromImage:
    async def test_archivo_inexistente_devuelve_none(self, tmp_path):
        missing = tmp_path / "no_existe.jpg"
        assert await ocr.extract_text_from_image(str(missing)) is None

    async def test_ocr_ok(self, monkeypatch, tmp_path):
        img = tmp_path / "boleto.jpg"
        img.write_bytes(b"\xff\xd8\xff")  # cabecera JPEG cualquiera

        monkeypatch.setattr(ocr, "_ocr_gemini", AsyncMock(return_value="STAKE 2"))
        monkeypatch.setattr(
            ocr,
            "get_settings",
            lambda: type("S", (), {"google_api_key": "g"})(),
        )
        assert await ocr.extract_text_from_image(str(img)) == "STAKE 2"

    async def test_todo_falla_devuelve_none(self, monkeypatch, tmp_path):
        """Un fallo de TODA la cadena no tumba el pipeline: el raw
        queda sin OCR y el ciclo de rescate lo reintentará."""
        img = tmp_path / "boleto.jpg"
        img.write_bytes(b"\xff\xd8\xff")

        monkeypatch.setattr(ocr, "_ocr_gemini", AsyncMock(return_value=None))
        monkeypatch.setattr(ocr, "_ocr_compat_vision", AsyncMock(return_value=None))
        monkeypatch.setattr(
            ocr,
            "get_settings",
            lambda: type("S", (), {"google_api_key": "g"})(),
        )
        assert await ocr.extract_text_from_image(str(img)) is None
