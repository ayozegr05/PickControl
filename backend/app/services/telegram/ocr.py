"""OCR para imágenes recibidas por Telegram.

De momento usa la API de OpenAI (gpt-4o-mini) si hay `OPENAI_API_KEY`.
Se puede extender con pytesseract u otro proveedor de OCR sin cambiar
la interfaz pública.
"""

import base64
import os

from app.core.logging import get_logger
from app.services.telegram.openai_retry import call_with_retry

logger = get_logger("app.telegram.ocr")


def _encode_image(image_path: str) -> str:
    """Codifica una imagen en base64 para enviarla a OpenAI."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


async def extract_text_from_image(image_path: str, api_key: str | None) -> str | None:
    """Extrae texto de una imagen.

    Si no hay `api_key`, simplemente devuelve None y se deja la imagen
    guardada para procesarla manualmente más tarde.
    """
    if not api_key:
        logger.info("No hay OPENAI_API_KEY; se omite OCR para %s", image_path)
        return None

    if not os.path.exists(image_path):
        logger.warning("El archivo %s no existe; no se puede hacer OCR", image_path)
        return None

    try:
        # Importar dentro de la función para que el proyecto arranque sin openai
        # si no se usa OCR.
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key)
        b64_image = _encode_image(image_path)
        data_url = f"data:image/jpeg;base64,{b64_image}"

        response = await call_with_retry(
            lambda: client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Extrae TODO el texto visible de la imagen. "
                                    "Devuelve solo el texto plano, sin comentarios."
                                ),
                            },
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    }
                ],
                max_tokens=2000,
            ),
            f"OCR {image_path}",
        )

        extracted = response.choices[0].message.content
        logger.info("OCR completado para %s", image_path)
        return extracted
    except Exception as exc:  # noqa: BLE001
        logger.error("Error haciendo OCR en %s: %s", image_path, exc)
        return None
