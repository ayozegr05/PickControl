"""OCR para imágenes recibidas por Telegram.

Cadena: OpenAI (gpt-4o-mini) si hay `OPENAI_API_KEY` → Gemini vision
(free tier, `GOOGLE_API_KEY`) cuando OpenAI falla o no hay key — un
billing agotado no puede parar la ingesta. Mismo prompt en ambos:
el texto extraído alimenta el mismo extractor.
"""

import base64
import os
from datetime import datetime, timedelta

import httpx

from app.core.config import get_settings
from app.core.dates import utc_now
from app.core.logging import get_logger
from app.services.results.base import (
    count_provider_call,
    count_purpose_call,
    is_rate_limited,
    mark_cooldown,
    retry_hint_seconds,
)
from app.services.telegram.openai_retry import call_with_retry

logger = get_logger("app.telegram.ocr")

_OCR_PROMPT = (
    "Extrae TODO el texto visible de la imagen, "
    "incluidos los distintivos pequeños: si junto "
    "a cada selección hay un check/tick (✓ o ✅) "
    "transcríbelo literalmente, y si junto a los "
    "equipos aparece un marcador final tipo "
    "'Marcador 0-2' transcribe también ese texto. "
    "Devuelve solo el texto plano, sin comentarios. "
    "Si la imagen es un boleto de apuestas ya "
    "liquidado/cobrado escribe como primera línea "
    "'SELLO: GANADOR'. Lo reconocerás por: la "
    "palabra GANADOR/GANADA/WON, un premio ya "
    "pagado, selecciones marcadas con ticks de "
    "color (habitualmente verde), marcadores "
    "finales junto a los equipos, o un pie con el "
    "importe cobrado ('54000€ Ganancias') sin el "
    "botón 'Cerrar apuesta' que tienen las "
    "apuestas aún abiertas."
)

# Modelos vision en orden de preferencia (misma familia que usa
# gemini_research; la cuota del free tier es por modelo).
_GEMINI_MODELS = (
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-3.8-flash",
)
_GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/" "{model}:generateContent"
)
_GEMINI_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
# Cuota por modelo (mismo patrón que gemini_research._MODEL_QUOTA_UNTIL):
# un 429 aparca solo ese modelo hasta el hint del retry, no la familia.
_GEMINI_QUOTA_UNTIL: dict[str, datetime] = {}
_GEMINI_429_DEFAULT = timedelta(minutes=20)

# Tercer peldaño de la cadena: endpoints OpenAI-compatible con
# visión. La cuota DIARIA de Gemini la comparten OCR y
# gemini_research (verificación de resultados) — cuando research la
# agota, los 3 modelos quedan aparcados hasta el reset y el OCR
# moría horas. Estos endpoints usan keys distintas (Groq/OpenRouter)
# y no compiten por ese presupuesto.
_VISION_ENDPOINTS = (
    # name, base_url, model, env_key
    (
        "groq-vision-scout",
        "https://api.groq.com/openai/v1",
        "meta-llama/llama-4-scout-17b-16e-instruct",
        "groq_api_key",
    ),
    (
        "groq-vision-maverick",
        "https://api.groq.com/openai/v1",
        "meta-llama/llama-4-maverick-17b-128e-instruct",
        "groq_api_key",
    ),
    (
        "openrouter-vision",
        "https://openrouter.ai/api/v1",
        "google/gemma-3-27b-it:free",
        "openrouter_api_key",
    ),
)
_VISION_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_VISION_429_FALLBACK = timedelta(minutes=20)
_VISION_ERROR_COOLDOWN = timedelta(minutes=5)


def _vision_mark(name: str) -> str:
    return f"ocr-vision-{name}"


def _encode_image(image_path: str) -> str:
    """Codifica una imagen en base64 para enviarla al modelo vision."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


async def _ocr_openai(image_path: str, api_key: str) -> str | None:
    """OCR vía OpenAI vision. Devuelve None si la llamada falla —
    quien llama prueba el fallback."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key)
    data_url = f"data:image/jpeg;base64,{_encode_image(image_path)}"
    response = await call_with_retry(
        lambda: client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _OCR_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            max_tokens=2000,
        ),
        f"OCR {image_path}",
    )
    extracted = response.choices[0].message.content
    logger.info("OCR completado (openai) para %s", image_path)
    return extracted


async def _ocr_gemini(image_path: str, api_key: str) -> str | None:
    """OCR vía Gemini vision REST (inline_data base64). Recorre los
    modelos en orden: un 429 aparca solo ese modelo, no la familia."""
    b64 = _encode_image(image_path)
    body = {
        "contents": [
            {
                "parts": [
                    {"text": _OCR_PROMPT},
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": b64,
                        }
                    },
                ]
            }
        ],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 2000},
    }
    for model in _GEMINI_MODELS:
        until = _GEMINI_QUOTA_UNTIL.get(model)
        if until is not None and until > utc_now():
            continue
        try:
            async with httpx.AsyncClient(timeout=_GEMINI_TIMEOUT) as client:
                resp = await client.post(
                    _GEMINI_ENDPOINT.format(model=model),
                    json=body,
                    headers={"x-goog-api-key": api_key},
                )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                secs = retry_hint_seconds(exc.response)
                _GEMINI_QUOTA_UNTIL[model] = utc_now() + (
                    timedelta(seconds=secs) if secs else _GEMINI_429_DEFAULT
                )
                logger.warning("[OCR] gemini %s 429 — probando siguiente modelo", model)
                continue
            logger.warning("[OCR] gemini %s HTTP %s", model, exc.response.status_code)
            continue
        except httpx.HTTPError as exc:
            logger.warning("[OCR] gemini %s error: %s", model, exc)
            continue
        try:
            text = (resp.json()["candidates"][0]["content"]["parts"][0]["text"]).strip()
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.warning("[OCR] gemini %s respuesta ilegible: %s", model, exc)
            continue
        if text:
            logger.info("OCR completado (gemini/%s) para %s", model, image_path)
            return text
    return None


async def _ocr_compat_vision(image_path: str) -> str | None:
    """OCR vía endpoints OpenAI-compatible con visión (Groq llama-4,
    OpenRouter :free). Mismo patrón de cooldown que compat_llm: un
    429 aparca solo ese endpoint hasta el hint del retry."""
    b64 = _encode_image(image_path)
    settings = get_settings()
    for name, base_url, model, env_key in _VISION_ENDPOINTS:
        key = getattr(settings, env_key, None)
        mark = _vision_mark(name)
        if not key or is_rate_limited(mark):
            continue
        count_provider_call(mark)
        count_purpose_call(mark, "ingesta")
        try:
            async with httpx.AsyncClient(timeout=_VISION_TIMEOUT) as client:
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": model,
                        "temperature": 0,
                        "max_tokens": 2000,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": _OCR_PROMPT},
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": f"data:image/jpeg;base64,{b64}"
                                        },
                                    },
                                ],
                            }
                        ],
                    },
                )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                secs = retry_hint_seconds(exc.response)
                mark_cooldown(
                    mark,
                    utc_now()
                    + (timedelta(seconds=secs) if secs else _VISION_429_FALLBACK),
                )
                logger.warning("[OCR] %s 429 — probando siguiente endpoint", name)
            else:
                mark_cooldown(mark, utc_now() + _VISION_ERROR_COOLDOWN)
                logger.warning(
                    "[OCR] %s HTTP %s — pausa 5m", name, exc.response.status_code
                )
            continue
        except httpx.HTTPError as exc:
            mark_cooldown(mark, utc_now() + _VISION_ERROR_COOLDOWN)
            logger.warning("[OCR] %s error: %s — pausa 5m", name, exc)
            continue
        try:
            text = (resp.json()["choices"][0]["message"]["content"] or "").strip()
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.warning("[OCR] %s respuesta ilegible: %s", name, exc)
            continue
        if text:
            logger.info("OCR completado (%s) para %s", name, image_path)
            return text
    return None


async def extract_text_from_image(image_path: str, api_key: str | None) -> str | None:
    """Extrae texto de una imagen.

    OpenAI primero si hay `api_key`; si falla (o no hay key) cae a
    Gemini vision con `GOOGLE_API_KEY`. Sin ninguno de los dos,
    devuelve None y la imagen queda guardada para el ciclo de rescate.
    """
    if not os.path.exists(image_path):
        logger.warning("El archivo %s no existe; no se puede hacer OCR", image_path)
        return None

    if api_key:
        try:
            text = await _ocr_openai(image_path, api_key)
            if text:
                return text
        except Exception as exc:  # noqa: BLE001
            logger.error("Error haciendo OCR openai en %s: %s", image_path, exc)

    google_key = get_settings().google_api_key
    if google_key:
        text = await _ocr_gemini(image_path, google_key)
        if text:
            return text

    # Gemini comparte cuota diaria con la verificación de resultados:
    # cuando está aparcado, la visión compat (keys aparte) salva el OCR.
    text = await _ocr_compat_vision(image_path)
    if text:
        return text

    if not api_key and not google_key:
        logger.info(
            "Ni OPENAI_API_KEY ni GOOGLE_API_KEY; se omite OCR para %s", image_path
        )
    return None
