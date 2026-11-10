#!/usr/bin/env bash
# Pocket of Paradise Travel Company — deploy/update on cPanel (Phusion Passenger) or any Linux host.
# Usage (from the project folder, with the app's virtualenv active):  ./deploy.sh
set -euo pipefail
cd "$(dirname "$0")"
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-days_safaris.settings.prod}"

echo "→ Installing Python packages"
pip install -q -r requirements.txt
if grep -q "^DB_MYSQL_DRIVER=pymysql" .env 2>/dev/null; then pip install -q -r requirements-mysql.txt; fi

echo "→ Checking database connection"
python manage.py check_database

echo "→ Applying migrations"
python manage.py migrate --noinput

echo "→ Collecting static files"
python manage.py collectstatic --noinput --clear -v 0

echo "→ Deployment checks"
python manage.py check --deploy --fail-level ERROR

mkdir -p tmp logs media
touch tmp/restart.txt
echo "✓ Deployed. Passenger will restart on the next request."
echo "  Remember to bump PWA_VERSION in .env when static files change, so installed apps update."
