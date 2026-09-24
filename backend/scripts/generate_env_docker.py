r"""Genera un `.env.docker` a partir del `.env` local.

Docker Compose utiliza un parser de `env_file` más estricto que
`python-dotenv`. Si `.env` contiene valores multilínea (p. ej. un
`JWT_SECRET` en formato PEM), este script crea un `.env.docker` limpio
con todos los valores en una sola línea.

Copia TODAS las claves del `.env` excepto `DATABASE_URL` (la sobrescribe
docker-compose con la URL del contenedor `db`).

Uso:
    .venv\Scripts\python.exe scripts\generate_env_docker.py
"""

import os

from dotenv import dotenv_values

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, ".env")
TARGET = os.path.join(ROOT, ".env.docker")

SKIP = {"DATABASE_URL"}


def main() -> None:
    values = dotenv_values(SOURCE)

    with open(TARGET, "w", encoding="utf-8") as f:
        for key, value in values.items():
            if key in SKIP or value is None:
                continue
            # El parser de env_file no admite valores multilínea.
            f.write(f"{key}={value.replace(chr(13), '').replace(chr(10), '')}\n")

    print(f"Creado {TARGET} con {sum(1 for k in values if k not in SKIP)} claves")


if __name__ == "__main__":
    main()
