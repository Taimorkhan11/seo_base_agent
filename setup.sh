#!/usr/bin/env bash
# Quick-start script for local development (Linux / macOS / WSL)
set -e

echo "==> Creating virtual environment..."
python3 -m venv .venv
source .venv/bin/activate

echo "==> Installing dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

echo "==> Copying .env.example → .env (if not present)..."
[ -f .env ] || cp .env.example .env

echo "==> Initialising database migrations..."
flask db init 2>/dev/null || true   # skip if migrations/ already exists
flask db migrate -m "Initial migration" 2>/dev/null || true
flask db upgrade

echo ""
echo "✓  Setup complete."
echo ""
echo "Next steps:"
echo "  1. Add your ANTHROPIC_API_KEY (and optionally DATAFORSEO_LOGIN/PASSWORD) to .env"
echo "  2. Run:  source .venv/bin/activate && flask run"
echo "  3. API is available at http://localhost:5000/api/v1"
