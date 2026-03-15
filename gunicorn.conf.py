import os

# Force ASGI worker for FastAPI apps.
worker_class = "uvicorn.workers.UvicornWorker"

# Render provides PORT at runtime.
bind = f"0.0.0.0:{os.getenv('PORT', '10000')}"

# Conservative defaults for small services; tunable via env vars.
workers = int(os.getenv("WEB_CONCURRENCY", "1"))
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))

accesslog = "-"
errorlog = "-"
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")
