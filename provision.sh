#!/usr/bin/env bash
# =============================================================================
# provision.sh  —  Stand up NVIDIA PersonaPlex (Moshi full-duplex speech-to-speech)
#                  on a vast.ai GPU instance, hands-free.
#
# Works on both Ada (RTX 4090) and Blackwell (RTX 5090) GPUs.
#
# PREREQUISITES (one-time, on huggingface.co):
#   1. Have a HuggingFace account.
#   2. Accept the model license for each model in models.json:
#        https://huggingface.co/kyutai/personaplex-rl-seamless   (the default)
#        https://huggingface.co/nvidia/personaplex-7b-v1
#   3. Create a READ token at:
#        https://huggingface.co/settings/tokens
#
#   (optional) For the in-UI "Shut down instance" button, also set:
#        VAST_API_KEY=<your vast.ai account API key>   (https://cloud.vast.ai/manage-keys/)
#
#   (optional) To cap what the rental can cost you, set VAST_API_KEY as above and:
#        MAX_RUNTIME_HOURS=24      (the instance destroys itself 24h after boot)
#
#   (optional) To ship a prompt of your own — the UI starts on it, and offers
#   it as the "Customized" preset:
#        SYSTEM_PROMPT="You work for ..."   (ignored if <= 8 characters)
#
# HOW THIS RUNS:
#
#   Normally you never invoke this yourself — spin_up.py passes it to vast.ai
#   as the instance's On-start Script, so it runs automatically at boot. (You
#   can also paste this whole file into the console's "On-start Script" box by
#   hand; set HF_TOKEN as an instance env var and expose port 8998.)
#
#   It is also safe to run directly on the instance when debugging, which is
#   the only way to watch it work in real time:
#        export HF_TOKEN=hf_xxxxxxxx
#        bash provision.sh
#   Re-running is idempotent — an already-cloned repo and installed deps are
#   detected and skipped.
#
# IMPORTANT: rent a "datacenter:" offer (not a plain "host:" one). Many hobbyist
# "host:" machines have firewalled outbound internet and cannot download the model
# from HuggingFace. This script egress-tests before doing anything.
# =============================================================================
set -euo pipefail

PORT="${MOSHI_PORT:-8998}"
# The model serve.py pre-loads at boot — prefetched first and gating, while
# every other model in models.json is warmed in the background afterwards.
# Must match DEFAULT_REPO in serve.py: this script starts the download before
# the app repo is even cloned, so it cannot read the value from there.
DEFAULT_REPO="${DEFAULT_REPO:-kyutai/personaplex-rl-seamless}"
REPO_DIR="${REPO_DIR:-/workspace/personaplex}"
APP_DIR="${APP_DIR:-/workspace/Roscommon-Full-Duplex-Test}"
APP_REPO="${APP_REPO:-github.com/roscommonsystems/Roscommon-Full-Duplex-Test}"
VENV="${VENV:-/venv/main}"
export HF_HOME="${HF_HOME:-/workspace/.hf_home}"

# --- Step timing -----------------------------------------------------------
# Every banner is also a stopwatch split: log() closes out the previous step
# and records how long it took, so timing_report() at the end says where the
# boot actually went. Bash's $SECONDS counts from script start, so this costs
# no `date` calls and no subshells.
#
# What this canNOT see is the docker image pull — that finishes before this
# script is even handed to the container. spin_up.py times that half (rented
# -> running) and prints both together.
STEP_START=$SECONDS
STEP_NAME=""
TIMINGS=()

# Close the running step, if any, and start one named $1. An empty $1 just
# closes the last step (what timing_report does before printing).
record_step(){
  if [ -n "$STEP_NAME" ]; then
    TIMINGS+=("$((SECONDS - STEP_START))|$STEP_NAME")
  fi
  STEP_START=$SECONDS
  STEP_NAME="${1:-}"
}

log(){
  record_step "$*"
  printf '\n=== [%dm%02ds] %s ===\n' "$((SECONDS / 60))" "$((SECONDS % 60))" "$*"
}

timing_report(){
  record_step ""
  echo
  printf '=== Where the time went (total %dm%02ds) ===\n' \
    "$((SECONDS / 60))" "$((SECONDS % 60))"
  echo "    (excludes the image pull, which happens before this script runs)"
  local entry
  for entry in ${TIMINGS[@]+"${TIMINGS[@]}"}; do
    printf '  %4ds  %s\n' "${entry%%|*}" "${entry#*|}"
  done
}

# --- Self-destruct timer ----------------------------------------------------
# MAX_RUNTIME_HOURS is a wall-clock budget for the whole rental: a detached
# watchdog waits it out, then DELETEs this instance through the vast API, which
# stops billing. spin_up.py sets it as a container env var; unset (or 0) means
# no limit, which is also what you get pasting this script in by hand.
#
# Armed here — before the checks below, before anything can fail — rather than
# once the server is up, because the runs that most need a backstop are the
# ones that never get there: a box that exits on a missing HF_TOKEN or a
# firewalled host bills exactly like one serving the demo, and has nothing
# running on it to notice.
#
# The deadline is kept on the instance's disk, so if the container restarts the
# watchdog resumes the original deadline instead of granting a fresh budget.
SELF_DESTRUCT_DEADLINE_FILE=/workspace/self_destruct_deadline
SELF_DESTRUCT_PID_FILE=/workspace/self_destruct.pid
SELF_DESTRUCT_LOG=/workspace/self_destruct.log

arm_self_destruct(){
  local hours="${MAX_RUNTIME_HOURS:-}" id deadline now
  # awk, not bash arithmetic: the budget may be fractional (0.5 = 30 min).
  [ -n "$hours" ] && [ "$(awk -v h="$hours" 'BEGIN{print (h + 0 > 0)}')" = "1" ] || return 0

  # Same resolution order as supervisor/teardown.py (the UI's Shut down
  # button): vast sets CONTAINER_ID, and when it doesn't, the id is the number
  # inside VAST_CONTAINERLABEL ("C.12345678").
  id="${CONTAINER_ID:-}"
  if [ -z "$id" ]; then
    id="$(printf '%s' "${VAST_CONTAINERLABEL:-}" | grep -o '[0-9][0-9]*' | head -n1)" || true
  fi
  if [ -z "${VAST_API_KEY:-}" ] || [ -z "$id" ]; then
    echo "WARNING: MAX_RUNTIME_HOURS=$hours is set, but VAST_API_KEY and this"
    echo "         instance's id are not both available — NO self-destruct armed."
    echo "         This instance bills until you destroy it by hand."
    return 0
  fi
  export VAST_API_KEY   # the watchdog reads it from the environment, see below

  if [ -s "$SELF_DESTRUCT_PID_FILE" ] && kill -0 "$(cat "$SELF_DESTRUCT_PID_FILE")" 2>/dev/null; then
    echo "Self-destruct watchdog already running (pid $(cat "$SELF_DESTRUCT_PID_FILE"))."
    return 0
  fi

  mkdir -p /workspace
  now="$(date -u +%s)"
  if [ -s "$SELF_DESTRUCT_DEADLINE_FILE" ]; then
    deadline="$(cat "$SELF_DESTRUCT_DEADLINE_FILE")"
  else
    deadline="$(awk -v n="$now" -v h="$hours" 'BEGIN{printf "%d", n + h * 3600}')"
    echo "$deadline" > "$SELF_DESTRUCT_DEADLINE_FILE"
  fi

  # The key is inherited through the environment rather than passed as an
  # argument: arguments are visible in `ps` to everything else on the box.
  nohup bash -c '
    deadline="$1"; id="$2"
    while [ "$(date -u +%s)" -lt "$deadline" ]; do sleep 30; done
    echo "$(date -u +%FT%TZ) budget expired — destroying instance $id"
    code="$(curl -s -m 30 -o /tmp/self_destruct_response -w "%{http_code}" \
      -X DELETE -H "Authorization: Bearer $VAST_API_KEY" \
      "https://console.vast.ai/api/v0/instances/$id/")"
    echo "$(date -u +%FT%TZ) vast API HTTP $code: $(cat /tmp/self_destruct_response)"
  ' _ "$deadline" "$id" >> "$SELF_DESTRUCT_LOG" 2>&1 &
  echo $! > "$SELF_DESTRUCT_PID_FILE"

  echo "Self-destruct armed (${hours}h budget): this instance destroys itself at"
  echo "  $(date -u -d "@$deadline" '+%Y-%m-%d %H:%M UTC').  Log: $SELF_DESTRUCT_LOG"
  echo "  Call it off with: kill \$(cat $SELF_DESTRUCT_PID_FILE); rm -f $SELF_DESTRUCT_DEADLINE_FILE"
}
arm_self_destruct

# --- 0. Checks -------------------------------------------------------------
if [ -z "${HF_TOKEN:-}" ]; then
  echo "ERROR: HF_TOKEN is not set."
  echo "  1) Accept the license for each model in models.json:"
  echo "       https://huggingface.co/kyutai/personaplex-rl-seamless (default)"
  echo "       https://huggingface.co/nvidia/personaplex-7b-v1"
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

# --- Model prefetch, in the background --------------------------------------
# The ~16GB model download is the long pole of the whole boot, and nothing
# below needs it — so start it first and let apt/pip/npm run underneath it.
# hf_transfer multi-streams the download; the stock client is single-stream
# and rarely clears ~40MB/s, which alone is 6+ minutes of the wait.
# serve.py reads the same HF_HOME cache, so a finished prefetch means the
# server finds every file local; a failed one just means the server downloads
# whatever is missing itself, exactly as before.
log "Starting model download in the background (~16GB, overlaps the installs below)"
uv pip install -q 'huggingface-hub>=0.24,<0.25' hf_transfer
export HF_HUB_ENABLE_HF_TRANSFER=1
( huggingface-cli download "$DEFAULT_REPO" \
    > /workspace/model_download.log 2>&1 ) &
MODEL_DL_PID=$!

log "Installing system dependencies (opus, portaudio, openssl)"
apt-get update -qq || true
apt-get install -y -qq libopus-dev libportaudio2 openssl

log "Cloning PersonaPlex repository"
if [ ! -d "$REPO_DIR/.git" ]; then
  git clone --depth 1 https://github.com/NVIDIA/personaplex "$REPO_DIR"
else
  echo "Repo already present at $REPO_DIR — skipping clone."
fi

# --- Our app repo ----------------------------------------------------------
# Needed for serve.py, supervisor/, models.json and the client. The repo is
# public, so this clones with no credentials. When running this script manually
# you've usually already cloned it (then this is a no-op).
log "Fetching the Roscommon app repo ($APP_DIR)"
if [ ! -d "$APP_DIR/.git" ]; then
  git clone --depth 1 "https://${APP_REPO}" "$APP_DIR"
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

# Our own runtime deps (aiohttp, huggingface-hub, numpy, faster-whisper,
# onnxruntime). requirements.txt repeats the pins from the block above, so
# resolving faster-whisper here can't drag huggingface-hub past the version
# moshi tolerates. The overlapping packages are already satisfied — no-ops.
log "Installing the app's runtime dependencies (requirements.txt)"
uv pip install -r "$APP_DIR/requirements.txt"

log "Installing CUDA libs for faster-whisper's GPU path (isolated from moshi's torch)"
uv pip install nvidia-cublas-cu12 nvidia-cudnn-cu12

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

log "Waiting for the background model download to finish"
if wait "$MODEL_DL_PID"; then
  echo "Model cache is warm."
else
  echo "Prefetch exited nonzero — the server will fetch whatever is missing itself."
  tail -n 5 /workspace/model_download.log || true
fi

# --- Prefetch the other models from models.json (background, non-gating) ----
# The UI can switch models, and a switch re-launches moshi.server on the new
# repo — blocking until its weights are local. Warming them now makes that
# switch near-instant. Started only *after* the default model finished, so the
# two never split the pipe while the boot is gated on the default; and fully
# detached, so provisioning neither waits on them nor fails with them (e.g. a
# model whose HF license hasn't been accepted just logs and stays cold).
EXTRA_MODELS="$(python - "$APP_DIR/models.json" "$DEFAULT_REPO" <<'PY' || true
import json, sys
for m in json.load(open(sys.argv[1])):
    if m["id"] != sys.argv[2]:   # already prefetched above, and gating
        print(m["id"])
PY
)"
for repo in $EXTRA_MODELS; do
  log "Prefetching $repo in the background (does not block startup)"
  nohup huggingface-cli download "$repo" \
      >> /workspace/model_prefetch_extra.log 2>&1 &
done

# --- Launch ----------------------------------------------------------------
log "Writing run script and launching server (tmux session 'moshi')"
cat > /workspace/run_moshi.sh <<EOF
#!/bin/bash
source $VENV/bin/activate
export HF_TOKEN=$HF_TOKEN
export HF_HOME=$HF_HOME
# Multi-stream HF downloads (installed above) — matters again when switching
# to a model that isn't cached yet, e.g. the base model from models.json.
export HF_HUB_ENABLE_HF_TRANSFER=1
export VAST_API_KEY=${VAST_API_KEY:-}
export CONTAINER_ID=${CONTAINER_ID:-}
export VAST_CONTAINERLABEL=${VAST_CONTAINERLABEL:-}
# The deployment's own prompt: the UI starts on it and offers it as the
# "Customized" preset. Quoted with
# %q, unlike the tokens above, because it is free text: spaces and apostrophes
# are the normal case here and would otherwise break this generated script.
export SYSTEM_PROMPT=$(printf '%q' "${SYSTEM_PROMPT:-}")
# serve.py launches asr_server.py itself; both are configured by the constants
# at the top of those files (ENABLE_ASR / ASR_PORT and PORT / MODEL).
# Let CTranslate2 (faster-whisper) find the pip-installed CUDA libs for GPU.
PYSITE=\$(python -c "import site;print(site.getsitepackages()[0])" 2>/dev/null)
export LD_LIBRARY_PATH="\$PYSITE/nvidia/cublas/lib:\$PYSITE/nvidia/cudnn/lib:\${LD_LIBRARY_PATH:-}"
cd /workspace/Roscommon-Full-Duplex-Test
exec python serve.py
EOF
chmod +x /workspace/run_moshi.sh

tmux kill-session -t moshi 2>/dev/null || true
tmux new-session -d -s moshi 'bash /workspace/run_moshi.sh > /workspace/moshi.log 2>&1'

log "Waiting for model to load (cache is warm — this is mostly the load into VRAM)"
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

# Kept on disk as well as printed: vast's on-start output scrolls past in the
# console, and this is the one artefact worth reading again after the fact.
timing_report | tee /workspace/boot_timing.log

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
if [ -s "$SELF_DESTRUCT_DEADLINE_FILE" ]; then
  echo "Expires:    $(date -u -d "@$(cat "$SELF_DESTRUCT_DEADLINE_FILE")" '+%Y-%m-%d %H:%M UTC')" \
       "— self-destructs then (log: $SELF_DESTRUCT_LOG)"
fi
