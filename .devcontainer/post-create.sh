#!/bin/bash
set -e

echo "=== Odysseus DevContainer Post-Create Setup (rennyoure-hash) ==="

# Python environment
python -m pip install --upgrade pip
if [ -f requirements.txt ]; then
  pip install -r requirements.txt
fi
if [ -f requirements-optional.txt ]; then
  pip install -r requirements-optional.txt || true
fi

# Node (for any frontend parts)
if [ -f package.json ]; then
  npm install --legacy-peer-deps || true
fi

# Git config (already set globally, but ensure)
git config --global user.name "rennyoure-hash"
git config --global user.email "rennyoure-hash@users.noreply.github.com"

# Create .env from example if not exists
if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
  echo "Created .env from .env.example - please edit with your keys"
fi

echo "=== Basic setup complete ==="
echo "Run 'python -m uvicorn app:app --host 0.0.0.0 --port 7000' to start"
echo "Or use docker compose up -d if you prefer containers"
