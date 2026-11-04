"""Tests del fallback de OCR: Gemini vision primero, endpoints
OpenAI-compatible con visión (Groq/OpenRouter) detrás."""

import httpx
import pytest

from app.services.telegram import ocr as ocr_mod


@pytest.fixture
def image(tmp_path):
    f = tmp_path / "boleto.jpg"
    f.write_bytes(b"\xff\xd8\xff\xd9")  # JPEG mínimo
    return str(f)


class TestOcrChain:
    async def test_gemini_ok_no_llama_compat(self, monkeypatch, image):
        async def fake_gemini(path, key, prompt=""):
            return "TEXTO GEMINI"

        async def boom_vision(path, prompt=""):
            raise AssertionError("no debería caer a la visión compat")

        monkeypatch.setattr(ocr_mod, "_ocr_gemini", fake_gemini)
        monkeypatch.setattr(ocr_mod, "_ocr_compat_vision", boom_vision)
        monkeypatch.setattr(
            ocr_mod,
            "get_settings",
            lambda: type("S", (), {"google_api_key": "g"})(),
        )
        assert await ocr_mod.extract_text_from_image(image) == "TEXTO GEMINI"

    async def test_gemini_seco_cae_a_compat(self, monkeypatch, image):
        async def gemini_none(path, key, prompt=""):
            return None

        async def fake_vision(path, prompt=""):
            return "TEXTO VISION"

        monkeypatch.setattr(ocr_mod, "_ocr_gemini", gemini_none)
        monkeypatch.setattr(ocr_mod, "_ocr_compat_vision", fake_vision)
        monkeypatch.setattr(
            ocr_mod,
            "get_settings",
            lambda: type("S", (), {"google_api_key": "g"})(),
        )
        assert await ocr_mod.extract_text_from_image(image) == "TEXTO VISION"

    async def test_sin_google_key_va_directo_a_compat(self, monkeypatch, image):
        async def boom_gemini(path, key, prompt=""):
            raise AssertionError("sin google key no se llama a Gemini")

        async def fake_vision(path, prompt=""):
            return "TEXTO VISION"

        monkeypatch.setattr(ocr_mod, "_ocr_gemini", boom_gemini)
        monkeypatch.setattr(ocr_mod, "_ocr_compat_vision", fake_vision)
        monkeypatch.setattr(
            ocr_mod,
            "get_settings",
            lambda: type("S", (), {"google_api_key": None})(),
        )
        assert await ocr_mod.extract_text_from_image(image) == "TEXTO VISION"

    async def test_todo_falla_devuelve_none(self, monkeypatch, image):
        async def boom(path, key, prompt=""):
            raise RuntimeError("gemini roto")

        async def vision_none(path, prompt=""):
            return None

        monkeypatch.setattr(ocr_mod, "_ocr_gemini", boom)
        monkeypatch.setattr(ocr_mod, "_ocr_compat_vision", vision_none)
        monkeypatch.setattr(
            ocr_mod,
            "get_settings",
            lambda: type("S", (), {"google_api_key": "g"})(),
        )
        assert await ocr_mod.extract_text_from_image(image) is None

    async def test_archivo_inexistente_none(self, tmp_path):
        assert await ocr_mod.extract_text_from_image(str(tmp_path / "nope.jpg")) is None


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


class TestOcrCompatVision:
    """Tercer peldaño de la cadena OCR: endpoints OpenAI-compatible
    con visión (Groq llama-4, OpenRouter :free). Existe porque la
    cuota DIARIA de Gemini la quema gemini_research verificando
    resultados — cuando los 3 modelos quedan aparcados hasta el
    reset, el OCR de Telegram moría con ellos hasta las 08:00."""

    def _cliente_mock(self, monkeypatch, handler):
        real_client = httpx.AsyncClient
        monkeypatch.setattr(
            ocr_mod.httpx,
            "AsyncClient",
            lambda **kw: real_client(transport=httpx.MockTransport(handler)),
        )

    def _settings(self, **kw):
        base = {
            "groq_api_key": None,
            "openrouter_api_key": None,
        }
        base.update(kw)
        return type("S", (), base)()

    async def test_devuelve_texto_del_primer_endpoint(self, monkeypatch, image):
        def handler(request):
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "PICK OCR"}}]},
            )

        monkeypatch.setattr(
            ocr_mod, "get_settings", lambda: self._settings(openrouter_api_key="k")
        )
        self._cliente_mock(monkeypatch, handler)
        assert await ocr_mod._ocr_compat_vision(image) == "PICK OCR"

    async def test_envia_imagen_como_data_uri(self, monkeypatch, image):
        import json as jsonlib

        bodies = []

        def handler(request):
            bodies.append(jsonlib.loads(request.content))
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "X"}}]}
            )

        monkeypatch.setattr(
            ocr_mod, "get_settings", lambda: self._settings(openrouter_api_key="k")
        )
        self._cliente_mock(monkeypatch, handler)
        await ocr_mod._ocr_compat_vision(image)
        content = bodies[0]["messages"][0]["content"]
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")

    async def test_429_aparca_y_pasa_al_siguiente(self, monkeypatch, image):
        llamados = []

        def handler(request):
            import json as jsonlib

            body = jsonlib.loads(request.content)
            llamados.append(body["model"])
            if len(llamados) == 1:
                return httpx.Response(429, json={})
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "OK"}}]}
            )

        monkeypatch.setattr(
            ocr_mod, "get_settings", lambda: self._settings(openrouter_api_key="k")
        )
        self._cliente_mock(monkeypatch, handler)
        assert await ocr_mod._ocr_compat_vision(image) == "OK"
        # El primer endpoint de Groq quedó aparcado; el segundo respondió.
        assert len(llamados) == 2

    async def test_sin_keys_devuelve_none(self, image):
        assert await ocr_mod._ocr_compat_vision(image) is None


class TestCadenaCompleta:
    """`extract_text_from_image` cae a la visión compat cuando Gemini
    está aparcado (el caso real: cuota diaria quemada por research)."""

    async def test_gemini_seco_usa_compat_vision(self, monkeypatch, image):
        async def gemini_none(path, key, prompt=""):
            return None

        async def vision(path, prompt=""):
            return "TEXTO VISION"

        monkeypatch.setattr(ocr_mod, "_ocr_gemini", gemini_none)
        monkeypatch.setattr(ocr_mod, "_ocr_compat_vision", vision)
        monkeypatch.setattr(
            ocr_mod,
            "get_settings",
            lambda: type("S", (), {"google_api_key": "g"})(),
        )
        assert await ocr_mod.extract_text_from_image(image) == ("TEXTO VISION")

    async def test_todo_seco_devuelve_none(self, monkeypatch, image):
        async def gemini_none(path, key, prompt=""):
            return None

        async def vision_none(path, prompt=""):
            return None

        monkeypatch.setattr(ocr_mod, "_ocr_gemini", gemini_none)
        monkeypatch.setattr(ocr_mod, "_ocr_compat_vision", vision_none)
        monkeypatch.setattr(
            ocr_mod,
            "get_settings",
            lambda: type("S", (), {"google_api_key": "g"})(),
        )
        assert await ocr_mod.extract_text_from_image(image) is None
