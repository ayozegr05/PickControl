# Levanta el backend de ControlPick en modo desarrollo.
#
# Uso (desde backend/):
#   .\scripts\run_dev.ps1
#
# --reload-dir app es OBLIGATORIO: sin el, uvicorn vigila TODO backend/
# (scripts/, media/, la sesion de Telethon...) y se reinicia en cada
# escritura, matando el login de Telegram, disparando catch-ups extra
# y saturando el TPM de OpenAI con rafagas de OCR/LLM.
#
# Para el primer login de Telethon, ejecutar SIN --reload (ver AGENTS.md).
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --reload --reload-dir app
