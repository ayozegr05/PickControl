# Imagen de producción/desarrollo del backend FastAPI.
#
# Uso local:
#   docker build -t controlpick-backend .
#   docker run --rm -p 8000:8000 --env-file .env.development controlpick-backend
#
# Con Docker Compose (recomendado):
#   docker compose up --build
FROM python:3.12-slim-bookworm

WORKDIR /app

# Variables de entorno para Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Copia e instala dependencias primero para aprovechar la caché de Docker
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copia el resto del código del backend
COPY . .

# Puerto expuesto por Uvicorn
EXPOSE 8000

# Por defecto arranca Uvicorn. En desarrollo, docker-compose lo sobreescribe
# para montar el código en caliente si se desea.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
