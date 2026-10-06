"""Tests del fallback de OCR: OpenAI primero, Gemini vision detrás."""

import httpx
import pytest

from app.services.telegram import ocr as ocr_mod


@pytest.fixture
def image(tmp_path):
    f = tmp_path / "boleto.jpg"
    f.write_bytes(b"\xff\xd8\xff\xd9")  # JPEG mínimo
    return str(f)


class TestOcrChain:
    async def test_openai_ok_no_llama_gemini(self, monkeypatch, image):
        async def fake_openai(path, key):
            return "TEXTO OPENAI"

        async def fake_gemini(path, key):
            raise AssertionError("no debería llamar a Gemini")

        monkeypatch.setattr(ocr_mod, "_ocr_openai", fake_openai)
        monkeypatch.setattr(ocr_mod, "_ocr_gemini", fake_gemini)
        monkeypatch.setattr(
            ocr_mod,
            "get_settings",
            lambda: type("S", (), {"google_api_key": "g"})(),
        )
        assert await ocr_mod.extract_text_from_image(image, "openai-key") == (
            "TEXTO OPENAI"
        )

    async def test_openai_falla_cae_a_gemini(self, monkeypatch, image):
        async def boom(path, key):
            raise RuntimeError("429 credit_balance_exhausted")

        async def fake_gemini(path, key):
            return "TEXTO GEMINI"

        monkeypatch.setattr(ocr_mod, "_ocr_openai", boom)
        monkeypatch.setattr(ocr_mod, "_ocr_gemini", fake_gemini)
        monkeypatch.setattr(
            ocr_mod,
            "get_settings",
            lambda: type("S", (), {"google_api_key": "g"})(),
        )
        assert await ocr_mod.extract_text_from_image(image, "openai-key") == (
            "TEXTO GEMINI"
        )

    async def test_sin_key_openai_va_directo_a_gemini(self, monkeypatch, image):
        async def fake_gemini(path, key):
            assert key == "g"
            return "TEXTO GEMINI"

        monkeypatch.setattr(ocr_mod, "_ocr_gemini", fake_gemini)
        monkeypatch.setattr(
            ocr_mod,
            "get_settings",
            lambda: type("S", (), {"google_api_key": "g"})(),
        )
        assert await ocr_mod.extract_text_from_image(image, None) == ("TEXTO GEMINI")

    async def test_todo_falla_devuelve_none(self, monkeypatch, image):
        async def boom(path, key):
            raise RuntimeError("openai roto")

        async def gemini_none(path, key):
            return None

        monkeypatch.setattr(ocr_mod, "_ocr_openai", boom)
        monkeypatch.setattr(ocr_mod, "_ocr_gemini", gemini_none)
        monkeypatch.setattr(
            ocr_mod,
            "get_settings",
            lambda: type("S", (), {"google_api_key": "g"})(),
        )
        assert await ocr_mod.extract_text_from_image(image, "k") is None

    async def test_archivo_inexistente_none(self, tmp_path):
        assert (
            await ocr_mod.extract_text_from_image(str(tmp_path / "nope.jpg"), "k")
            is None
        )


class TestOcrGemini:
    """El endpoint REST de Gemini vision: payload inline_data y salto
    de modelo ante 429."""

    def _cliente_mock(self, monkeypatch, handler):
        real_client = httpx.AsyncClient
        monkeypatch.setattr(
            ocr_mod.httpx,
            "AsyncClient",
            lambda **kw: real_client(transport=httpx.MockTransport(handler)),
        )
        ocr_mod._GEMINI_QUOTA_UNTIL.clear()

    async def test_extrae_texto_del_payload(self, monkeypatch, image):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {"content": {"parts": [{"text": "Betis gana @2.0"}]}}
                    ]
                },
            )

        self._cliente_mock(monkeypatch, handler)
        assert await ocr_mod._ocr_gemini(image, "g") == "Betis gana @2.0"

    async def test_429_salta_al_siguiente_modelo(self, monkeypatch, image):
        llamados = []

        def handler(request):
            model = str(request.url).split("models/")[1].split(":")[0]
            llamados.append(model)
            if len(llamados) == 1:
                return httpx.Response(429, json={})
            return httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": "OK"}]}}]},
            )

        self._cliente_mock(monkeypatch, handler)
        assert await ocr_mod._ocr_gemini(image, "g") == "OK"
        assert len(llamados) == 2

    async def test_envia_imagen_inline(self, monkeypatch, image):
        bodies = []

        def handler(request):
            import json as jsonlib

            bodies.append(jsonlib.loads(request.content))
            return httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": "X"}]}}]},
            )

        self._cliente_mock(monkeypatch, handler)
        await ocr_mod._ocr_gemini(image, "g")
        parts = bodies[0]["contents"][0]["parts"]
        assert parts[1]["inline_data"]["mime_type"] == "image/jpeg"
        assert parts[1]["inline_data"]["data"]
