#!/bin/sh
# Starts the game: settings check, database migrations, then the server.
set -e
python -m terraforma check-settings
python -m terraforma migrate
exec uvicorn vanguard_tavern:app --factory --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'
