#!/bin/sh
set -e

echo "Waiting for database to be ready..."
python - <<'PY'
import os
import socket
import sys
import time

host = os.environ.get('DB_HOST', 'db')
port = int(os.environ.get('DB_PORT', 5432))
for _ in range(30):
    try:
        with socket.create_connection((host, port), timeout=2):
            sys.exit(0)
    except OSError:
        time.sleep(1)
sys.exit(1)
PY

echo "Running migrations..."
python manage.py migrate --no-input

echo "Collecting static files..."
python manage.py collectstatic --noinput

echo "Starting Gunicorn server..."
# Plusieurs fils : avec un seul, servir une vidéo ou recevoir un gros envoi
# bloquait toute l'API le temps du transfert. Le délai de 30 s par défaut est
# trop court pour une vidéo de 100 Mo. GUNICORN_CMD_ARGS permet de surcharger.
python -m gunicorn --bind 0.0.0.0:8000 --worker-class gthread --workers 2 --threads 4 --timeout 120 core.wsgi:application