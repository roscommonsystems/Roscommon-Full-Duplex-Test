#!/usr/bin/env bash
# =============================================================================
# provision.sh  —  Stand up NVIDIA PersonaPlex (Moshi full-duplex speech-to-speech)
#                  on a vast.ai GPU instance, hands-free.
#
# Works on both Ada (RTX 4090) and Blackwell (RTX 5090) GPUs.
#
# PREREQUISITES (one-time, on huggingface.co):
#   1. Have a HuggingFace account.
#   2. Accept the model license at:
#        https://huggingface.co/nvidia/personaplex-7b-v1
#   3. Create a READ token at:
#        https://huggingface.co/settings/tokens
#
#   (optional) For the in-UI "Shut down instance" button, also set:
#        VAST_API_KEY=<your vast.ai account API key>   (https://cloud.vast.ai/manage-keys/)
#
# HOW TO USE — pick ONE:
#
#   A) Manually, after SSHing into the instance:
#        export HF_TOKEN=hf_xxxxxxxx
#        bash provision.sh
#
#   B) As a vast.ai On-start Script:
#        - In the instance config, add an env var:  HF_TOKEN=hf_xxxxxxxx
#        - Paste this whole file into the "On-start Script" box.
#        - Make sure port 8998 is exposed in the Docker options.
#
# IMPORTANT: rent a "datacenter:" offer (not a plain "host:" one). Many hobbyist
# "host:" machines have firewalled outbound internet and cannot download the model
# from HuggingFace. This script egress-tests before doing anything.
# =============================================================================
set -euo pipefail

PORT="${MOSHI_PORT:-8998}"
REPO_DIR="${REPO_DIR:-/workspace/personaplex}"
APP_DIR="${APP_DIR:-/workspace/Roscommon-Full-Duplex-Test}"
APP_REPO="${APP_REPO:-github.com/roscommonsystems/Roscommon-Full-Duplex-Test}"
VENV="${VENV:-/venv/main}"
export HF_HOME="${HF_HOME:-/workspace/.hf_home}"

log(){ echo -e "\n=== $* ==="; }

# --- 0. Checks -------------------------------------------------------------
if [ -z "${HF_TOKEN:-}" ]; then
  echo "ERROR: HF_TOKEN is not set."
  echo "  1) Accept the license: https://huggingface.co/nvidia/personaplex-7b-v1"
  echo "  2) Make a READ token:  https://huggingface.co/settings/tokens"
  echo "  3) export HF_TOKEN=hf_xxxxxxxx   (then re-run)"
  exit 1
fi

log "Activating Python environment ($VENV)"
# shellcheck disable=SC1091
source "$VENV/bin/activate"

log "Egress check — can this host reach HuggingFace?"
code="$(curl -s -m 20 -o /dev/null -w '%{http_code}' https://huggingface.co || true)"
if [ "$code" != "200" ]; then
  echo "ERROR: cannot reach huggingface.co (HTTP '$code')."
  echo "This host has blocked/broken outbound internet. Destroy it and rent a"
  echo "'datacenter:' offer instead, then re-run."
  exit 1
fi
echo "HuggingFace reachable (HTTP 200)."

log "Installing system dependencies (opus, portaudio, openssl)"
apt-get update -qq || true
apt-get install -y -qq libopus-dev libportaudio2 openssl ffmpeg

log "Cloning PersonaPlex repository"
if [ ! -d "$REPO_DIR/.git" ]; then
  git clone --depth 1 https://github.com/NVIDIA/personaplex "$REPO_DIR"
else
  echo "Repo already present at $REPO_DIR — skipping clone."
fi

# --- Our app repo (private) ------------------------------------------------
# Needed for serve.py, supervisor/, models.json and the client. When running
# this script manually you've usually already cloned it (then this is a no-op).
# For hands-free vast.ai on-start, set GITHUB_TOKEN so the private repo can be
# cloned non-interactively.
log "Fetching the Roscommon app repo ($APP_DIR)"
if [ ! -d "$APP_DIR/.git" ]; then
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    git clone --depth 1 "https://${GITHUB_TOKEN}@${APP_REPO}" "$APP_DIR"
  else
    echo "ERROR: $APP_DIR is not present and GITHUB_TOKEN is not set."
    echo "The app repo is private. Either:"
    echo "  - clone it yourself to $APP_DIR first, then re-run; or"
    echo "  - set GITHUB_TOKEN=ghp_xxxxxxxx (a token with repo read access) and re-run."
    exit 1
  fi
else
  echo "App repo already present at $APP_DIR — pulling latest."
  git -C "$APP_DIR" pull --ff-only || echo "(pull skipped — local changes or detached HEAD)"
fi

# --- Blackwell-safe install ------------------------------------------------
# moshi pins torch>=2.2,<2.5 (a CUDA 12.1 build) which has NO kernels for
# Blackwell GPUs (RTX 5090, sm_120) and fails at runtime with
# "no kernel image is available". The vast PyTorch image already ships a
# Blackwell-capable torch (cu128). So we install moshi WITHOUT its deps to
# preserve that torch, then add the remaining deps explicitly.
log "Installing moshi (--no-deps to preserve GPU-compatible torch)"
uv pip install "$REPO_DIR/moshi" --no-deps

log "Installing moshi's other dependencies"
uv pip install \
  'numpy>=1.26,<2.2' \
  'safetensors>=0.4.0,<0.5' \
  'huggingface-hub>=0.24,<0.25' \
  'einops==0.7' \
  'sentencepiece==0.2' \
  'sounddevice==0.5' \
  'sphn>=0.1.4,<0.2' \
  'aiohttp>=3.10.5,<3.11'

# aiohttp (the supervisor's only extra runtime dep) is already installed above
# in the moshi deps block (pinned >=3.10.5,<3.11) — no separate install needed.

log "Installing faster-whisper (+ CUDA libs for GPU; isolated from moshi's torch)"
uv pip install faster-whisper onnxruntime nvidia-cublas-cu12 nvidia-cudnn-cu12

log "Installing FORKED moshi for the pharma puppeteer model (isolated venv /venv/pharma)"
# The pharma fine-tune needs the authors' forked moshi: it adds mid-call context
# injection (LMGen.inject_context + a JSON 'context' channel in server.py) that
# stock moshi lacks. We install it into a SEPARATE venv so it cannot clobber the
# stock moshi the other two models use. --system-site-packages lets it reuse the
# image's Blackwell-capable torch instead of reinstalling it.
PP_FT_DIR="${PP_FT_DIR:-/workspace/pp-finetune}"
if [ ! -d "$PP_FT_DIR/.git" ]; then
  git clone --depth 1 --filter=blob:none --sparse \
    https://github.com/emotion-machine-org/personaplex-finetune "$PP_FT_DIR"
  git -C "$PP_FT_DIR" sparse-checkout set personaplex/moshi
fi
python -m venv --system-site-packages /venv/pharma
# Install ONLY the forked moshi package (shadows stock moshi in THIS venv only),
# preserving the inherited torch. Its non-torch deps match the stock pins.
/venv/pharma/bin/pip install --no-deps "$PP_FT_DIR/personaplex/moshi"
/venv/pharma/bin/pip install \
  'numpy>=1.26,<2.2' 'safetensors>=0.4.0,<0.5' 'huggingface-hub>=0.24,<0.25' \
  'einops==0.7' 'sentencepiece==0.2' 'sounddevice==0.5' 'sphn>=0.1.4,<0.2' \
  'aiohttp>=3.10.5,<3.11'
echo "Forked moshi venv ready. Verify on the box with:"
echo "  /venv/pharma/bin/python -c \"import moshi, torch; print('cuda', torch.cuda.is_available())\""

log "Provisioning pharma reference voice (.wav) — finetuned model rejects base .pt voices"
# The finetuned pharma model only accepts a .wav reference voice (load_voice_prompt),
# not the base model's .pt embeddings. Grab a clean, openly-licensed studio voice
# (Kyutai tts-voices, CC-BY) and trim to ~12s mono. load_voice_prompt resamples to
# 32 kHz itself; -ac 1 keeps it mono.
PHARMA_VOICES="${PHARMA_VOICES:-/workspace/pharma_voices}"
mkdir -p "$PHARMA_VOICES"
if [ ! -f "$PHARMA_VOICES/pharma_voice.wav" ]; then
  VOICE_SRC="https://huggingface.co/kyutai/tts-voices/resolve/main/expresso/ex01-ex02_default_001_channel1_168s.wav"
  if curl -fsSL "$VOICE_SRC" -o /tmp/pharma_voice_src.wav; then
    ffmpeg -y -loglevel error -i /tmp/pharma_voice_src.wav -t 12 -ac 1 "$PHARMA_VOICES/pharma_voice.wav"
    rm -f /tmp/pharma_voice_src.wav
    echo "Pharma voice ready at $PHARMA_VOICES/pharma_voice.wav"
  else
    echo "WARNING: could not download the pharma reference voice; pharma will be silent until one is placed at $PHARMA_VOICES/pharma_voice.wav"
  fi
fi

log "Installing Node.js (to build the web client)"
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y -qq nodejs
fi
echo "node $(node --version) / npm $(npm --version)"

log "Building the web client ($APP_DIR/client)"
( cd "$APP_DIR/client" && npm install && npm run build )

log "Sanity check — torch sees the GPU"
python - <<'PY'
import torch
assert torch.cuda.is_available(), "CUDA not available!"
print("torch", torch.__version__, "| CUDA", torch.version.cuda,
      "| GPU capability", torch.cuda.get_device_capability())
PY

# --- Launch ----------------------------------------------------------------
log "Writing run script and launching server (tmux session 'moshi')"
cat > /workspace/run_moshi.sh <<EOF
#!/bin/bash
source $VENV/bin/activate
export HF_TOKEN=$HF_TOKEN
export HF_HOME=$HF_HOME
export VAST_API_KEY=${VAST_API_KEY:-}
export CONTAINER_ID=${CONTAINER_ID:-}
export VAST_CONTAINERLABEL=${VAST_CONTAINERLABEL:-}
export ASR_CMD="\${ASR_CMD:-python asr_server.py --port 8997 --model medium.en --device cuda --cpu-model small.en}"
# Let CTranslate2 (faster-whisper) find the pip-installed CUDA libs for GPU.
PYSITE=\$(python -c "import site;print(site.getsitepackages()[0])" 2>/dev/null)
export LD_LIBRARY_PATH="\$PYSITE/nvidia/cublas/lib:\$PYSITE/nvidia/cudnn/lib:\${LD_LIBRARY_PATH:-}"
cd /workspace/Roscommon-Full-Duplex-Test
exec python serve.py
EOF
chmod +x /workspace/run_moshi.sh

tmux kill-session -t moshi 2>/dev/null || true
tmux new-session -d -s moshi 'bash /workspace/run_moshi.sh > /workspace/moshi.log 2>&1'

log "Waiting for model to load (first run downloads ~16GB; up to ~10 min)"
ready=0
for _ in $(seq 1 90); do
  if grep -q "Model ready:" /workspace/moshi.log 2>/dev/null; then
    ready=1; break
  fi
  if grep -q "Model failed to load" /workspace/moshi.log 2>/dev/null; then
    echo "ERROR: model failed to load — see /workspace/moshi.log"
    break
  fi
  sleep 10
done

# --- Report ----------------------------------------------------------------
PUB_IP="${PUBLIC_IPADDR:-$(curl -s -m 10 https://api.ipify.org || echo UNKNOWN)}"
EXT_PORT="$(printenv "VAST_TCP_PORT_$PORT" || echo "$PORT")"

if [ "$ready" = "1" ]; then
  log "PersonaPlex is UP"
else
  log "Server not confirmed ready yet — check the log (it may still be loading)"
fi
echo "Web UI:     https://$PUB_IP:$EXT_PORT"
echo "            (self-signed cert: click through the browser warning, then allow mic)"
echo "Logs:       tail -f /workspace/moshi.log"
echo "Attach:     tmux attach -t moshi"
echo "Restart:    bash /workspace/run_moshi.sh   (or re-run this script)"
