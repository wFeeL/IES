FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY ies_bot_skeleton ./ies_bot_skeleton
COPY ies.py main.py ./

RUN python -m pip install --upgrade pip && \
    python -m pip install .

ENV IES_WEB_ENV=production \
    IES_WEB_HOST=0.0.0.0 \
    IES_WEB_PORT=5000

EXPOSE 5000

CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:5000", "ies_bot_skeleton.web.app:create_app()"]
