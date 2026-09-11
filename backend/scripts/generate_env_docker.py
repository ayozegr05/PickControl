r"""Genera un `.env.docker` a partir del `.env` local.

Docker Compose utiliza un parser de `env_file` más estricto que
`python-dotenv`. Si `.env` contiene valores multilínea (p. ej. un
`JWT_SECRET` en formato PEM), este script crea un `.env.docker` limpio
con todos los valores en una sola línea.

Uso:
    .venv\Scripts\python.exe scripts\generate_env_docker.py
"""

import os
import secrets

from dotenv import dotenv_values

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, ".env")
TARGET = os.path.join(ROOT, ".env.docker")

KEYS = [
    "NODE_ENV",
    "SERVER_URL",
    "TELEGRAM_API_ID",
    "TELEGRAM_API_HASH",
    "TELEGRAM_PHONE",
    "TELEGRAM_TARGET_CHANNEL",
    "TELEGRAM_SESSION_NAME",
    "TELEGRAM_MEDIA_PATH",
    "OPENAI_API_KEY",
]


def main() -> None:
    values = dotenv_values(SOURCE)

    out = {}
    for key in KEYS:
        if values.get(key):
            out[key] = values[key].replace("\r", "").replace("\n", "")

    # JWT_SECRET: si el original tiene saltos de línea, generamos uno nuevo.
    out["JWT_SECRET"] = secrets.token_urlsafe(64)

    # En Docker el backend conecta al contenedor `db`, no a localhost.
    out["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@db:5432/controlpick"

    with open(TARGET, "w", encoding="utf-8") as f:
        for key in KEYS + ["JWT_SECRET", "DATABASE_URL"]:
            if key in out:
                f.write(f"{key}={out[key]}\n")

    print(f"Creado {TARGET}")


if __name__ == "__main__":
    main()
