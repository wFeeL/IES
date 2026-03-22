#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

VENV_DIR="$ROOT_DIR/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"
PIP_BIN="$VENV_DIR/bin/pip"

APP_TARGET="ies_bot_skeleton.web.app:create_app"
MIGRATIONS_DIR="ies_bot_skeleton/web/migrations"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-5000}"
ENV_NAME="${IES_WEB_ENV:-development}"
RUN_MIGRATIONS=1
RUN_SEED=1
INSTALL_DEPS=1

usage() {
  cat <<'EOF'
Usage: ./run.sh [options]

Options:
  --host HOST         Flask host, default: 127.0.0.1
  --port PORT         Flask port, default: 5000
  --env NAME          IES_WEB_ENV, default: development
  --no-migrate        Skip flask db upgrade
  --no-seed           Skip flask seed
  --no-install        Skip dependency installation check
  -h, --help          Show this help

Environment overrides:
  HOST
  PORT
  IES_WEB_ENV
  IES_WEB_DATABASE_URL
  IES_WEB_SECRET_KEY
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      HOST="${2:?missing value for --host}"
      shift 2
      ;;
    --port)
      PORT="${2:?missing value for --port}"
      shift 2
      ;;
    --env)
      ENV_NAME="${2:?missing value for --env}"
      shift 2
      ;;
    --no-migrate)
      RUN_MIGRATIONS=0
      shift
      ;;
    --no-seed)
      RUN_SEED=0
      shift
      ;;
    --no-install)
      INSTALL_DEPS=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      echo >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ ! -d "$VENV_DIR" ]]; then
  echo "Creating virtual environment in $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python interpreter not found in $VENV_DIR" >&2
  exit 1
fi

if [[ "$INSTALL_DEPS" -eq 1 ]]; then
  if ! "$PYTHON_BIN" -c "import flask, flask_migrate, ies_bot_skeleton.web.app" >/dev/null 2>&1; then
    echo "Installing project dependencies into $VENV_DIR"
    "$PIP_BIN" install --upgrade pip
    "$PIP_BIN" install -e ".[dev]"
  fi
fi

export IES_WEB_ENV="$ENV_NAME"
export FLASK_APP="$APP_TARGET"

echo "Project root: $ROOT_DIR"
echo "Environment: $IES_WEB_ENV"
echo "Database: ${IES_WEB_DATABASE_URL:-sqlite:///$ROOT_DIR/ies_web.db}"

if [[ "$RUN_MIGRATIONS" -eq 1 ]]; then
  echo "Running database migrations"
  "$PYTHON_BIN" -m flask --app "$APP_TARGET" db upgrade -d "$MIGRATIONS_DIR"
fi

if [[ "$RUN_SEED" -eq 1 ]]; then
  echo "Seeding default users and reference data"
  "$PYTHON_BIN" -m flask --app "$APP_TARGET" seed
fi

echo "Starting server on http://$HOST:$PORT"
exec "$PYTHON_BIN" -m flask --app "$APP_TARGET" run --host "$HOST" --port "$PORT"
