#!/usr/bin/env bash
# Gemello Linux di setup.ps1: i due venv della pubblicazione, i Chromium di
# Playwright e cloudflared. Idempotente.
#
#   bash publish/setup.sh
#
# Prerequisiti di sistema (root, una volta, nel runbook di server-di-casa):
#   publish/.venv/bin/python -m playwright install-deps chromium
#   google-chrome-stable (YouTube usa il Chrome vero, vedi youtube_agent.py)
#   Xvfb su :99 (le finestre `headless=False` ci vanno sopra) e noVNC per i login
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
SHARED="$HOME/.venvs/scraping"
export PATH="$HOME/.local/bin:$PATH"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh

[ -x "$HERE/.venv/bin/python" ] || uv venv --python 3.12 "$HERE/.venv"
uv pip install --python "$HERE/.venv/bin/python" "playwright==1.62.0" "patchright==1.62.2"
"$HERE/.venv/bin/python" -m playwright install chromium

# Il venv condiviso dello scraping: serve anche al radar e alle metriche.
[ -x "$SHARED/bin/python" ] || uv venv --python 3.12 "$SHARED"
uv pip install --python "$SHARED/bin/python" "scrapling[fetchers]==0.4.15" "patchright==1.62.2"
# Dopo ogni installazione di Scrapling: ogni Playwright vuole il suo Chromium.
"$SHARED/bin/python" -m playwright install chromium
"$SHARED/bin/python" -m patchright install chromium

CF="$ROOT/.tools/cloudflared/cloudflared"
if [ ! -x "$CF" ]; then
    mkdir -p "$(dirname "$CF")"
    curl -fsSL -o "$CF" https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
    chmod +x "$CF"
fi
"$CF" --version

echo
echo "Fatto. Le credenziali non le crea questo script (file 600, mai in git):"
echo "  publish/.buffer/chiave.txt e publish/.meta/<gruppo>.txt"
echo "  i login di YouTube e Whop si fanno a mano nella postazione noVNC"
