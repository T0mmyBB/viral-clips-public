#!/usr/bin/env bash
# Setup una tantum della skill viral-clips su Linux (il server `lavoro`).
# Gemello di setup.ps1, idempotente come lui. Le versioni sono le stesse del
# portatile, fissate: il collaudo del 5 ottobre 2026 confronta le clip delle due
# macchine, e una libreria diversa e' una differenza che non viene dalla macchina.
#
#   bash .claude/skills/viral-clips/scripts/setup.sh
#
# Prerequisiti di sistema (root, una volta, nel runbook di server-di-casa):
#   apt install ffmpeg libgl1 libglib2.0-0t64
set -euo pipefail

SKILL_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_ROOT="$(cd "$SKILL_ROOT/../../.." && pwd)"
VENV="$PROJECT_ROOT/.venv"
MODELS="$SKILL_ROOT/assets/models"
FONTS="$SKILL_ROOT/assets/fonts"
export PATH="$HOME/.local/bin:$PATH"

step() { printf '\n=== %s ===\n' "$1"; }

step "1/5  ffmpeg"
command -v ffmpeg >/dev/null || { echo "manca ffmpeg: apt install ffmpeg (root)"; exit 1; }
ffmpeg -hide_banner -version | head -1

step "2/5  uv e Python 3.12"
# Il Python di sistema e' il 3.14: troppo recente per ctranslate2 e opencv,
# come su Windows. uv installa un 3.12 nella home, senza toccare il sistema.
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.12

step "3/5  venv e dipendenze"
[ -x "$VENV/bin/python" ] || uv venv --python 3.12 "$VENV"
uv pip install --python "$VENV/bin/python" \
    "faster-whisper==1.2.1" "ctranslate2==4.8.1" "opencv-python==5.0.0.93" \
    "numpy==2.5.2" "scenedetect==0.7.1" "av==18.1.0" "tokenizers==0.23.1" "ruff==0.16.9"
# yt-dlp sempre dalla prerelease: e' la prima cura di ogni problema YouTube (CLAUDE.md).
uv pip install --python "$VENV/bin/python" --prerelease allow -U "yt-dlp[default]"

step "4/5  YuNet e font"
mkdir -p "$MODELS/whisper" "$FONTS"
[ -s "$MODELS/face_detection_yunet.onnx" ] || curl -fsSL -o "$MODELS/face_detection_yunet.onnx" \
    https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
[ -s "$FONTS/ArchivoBlack-Regular.ttf" ] || curl -fsSL -o "$FONTS/ArchivoBlack-Regular.ttf" \
    https://github.com/google/fonts/raw/main/ofl/archivoblack/ArchivoBlack-Regular.ttf

step "5/5  modello Whisper large-v3-turbo"
"$VENV/bin/python" - "$MODELS/whisper" <<'EOF'
import sys
from faster_whisper import WhisperModel
WhisperModel("large-v3-turbo", device="cpu", compute_type="int8", download_root=sys.argv[1])
print("whisper large-v3-turbo pronto")
EOF

"$VENV/bin/python" -c "
import cv2, numpy, faster_whisper, ctranslate2, yt_dlp, scenedetect
cv2.FaceDetectorYN
print('opencv', cv2.__version__, '| ctranslate2', ctranslate2.__version__, '| yt-dlp', yt_dlp.version.__version__)
"
echo "Setup completato. Python del venv: $VENV/bin/python"
