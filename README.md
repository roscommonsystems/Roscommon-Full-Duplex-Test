# Roscommon Full-Duplex Test

Reproducible setup for running **NVIDIA PersonaPlex** — a 7B real-time, full-duplex
speech-to-speech conversational model (built on Kyutai's Moshi architecture) — on a
[vast.ai](https://vast.ai) GPU instance. Anyone on the team should be able to follow
this unaided.

The demo ships two models: **PersonaPlex (Original)** and **PersonaPlex RL Seamless**,
both selectable from the in-UI dropdown. One command does the whole thing:
[`spin_up.py`](./spin_up.py) rents a GPU and provisions it.

---

## 1. One-time prerequisites

### Hugging Face (per person)

1. Create a Hugging Face account.
2. **Accept the model licenses** (open each, click agree):
   - https://huggingface.co/nvidia/personaplex-7b-v1 (base)
   - https://huggingface.co/kyutai/personaplex-rl-seamless (RL seamless)
3. Create a **READ token**: https://huggingface.co/settings/tokens → this is your
   `HF_TOKEN`.

### vast.ai (per person)

1. Create a vast.ai account and **load credits**.
2. Create an **API key**: https://cloud.vast.ai/manage-keys/ → this is your
   `VAST_API_KEY`. Keys default to full account access —
   [`.env.example`](./.env.example) spells out the minimum permission grid to tick
   instead.
3. Add your **SSH public key**: Account → SSH Keys. Not needed to deploy, but it's how
   you read `/workspace/moshi.log` when a host misbehaves.

> Tokens and license acceptance are per-account — each person needs their own.
> (This repo is public, so nothing is needed to clone it.)

---

## 2. Deploy

```bash
cp .env.example .env    # fill in VAST_API_KEY and HF_TOKEN
python spin_up.py
```

It picks the cheapest offer matching the constants at the top of
[`spin_up.py`](./spin_up.py), rents it with `provision.sh` as the on-start script, waits
for the model to load, and prints the URL. Stdlib only — nothing to install, and you
never touch the vast.ai console.

**This spends money without further prompting** — billing starts the moment it rents.
`MAX_DOLLARS_PER_HOUR` is your ceiling; the offer list is printed before it commits.

### What it rents, and why

The constants encode the requirements you'd otherwise have to remember when picking a
box by hand — edit them in place if you need something different:

- **`GPU_NAME` / `MIN_GPU_RAM_GB`** — RTX 5090, 32 GB. The model uses ~19.5 GB and the
  live transcription (ASR) adds a few GB, so 32 GB is the practical minimum.
- **`DATACENTER_ONLY`** — many hobbyist `host:` machines have firewalled outbound
  internet and cannot download the model from Hugging Face at all.
- **`DISK_GB`** — ~16 GB of weights, plus CUDA wheels and the client build.
- **`IMAGE`** — must be a vast PyTorch image: `provision.sh` expects its `/venv/main`
  virtualenv and a Blackwell-capable torch.
- **`MAX_DOLLARS_PER_HOUR`** — your walk-away price.

Port 8998 is published for you. `provision.sh` egress-tests the host before doing
anything, so a firewalled one fails fast and loudly rather than halfway through a 16 GB
download.

---

## 3. Open the demo

1. `spin_up.py` prints the URL once the model is ready — open it.
2. The TLS cert is self-signed → the browser warns *"your connection is not private"* →
   **Advanced → Proceed**, then **Allow microphone**.

Pick a voice/role and start talking. It's real-time: just speak and it replies.

### Using it

- **Model dropdown** — pick PersonaPlex (Original) or RL Seamless. Switching reloads
  the model (~45 s; you'll see a "Loading…" screen). Original is the best all-round
  model to show; RL Seamless has smoother turn-taking (its license is non-commercial,
  internal/demo use only).
- **Text Prompt / Voice** — set the persona and voice, then **Connect** and talk.
- **Shut down instance** — button at the bottom, only shown if `VAST_API_KEY` was set.
  Destroys the instance and stops billing. Use this after a demo.

### Live transcription (the user's words)

The "You:" live transcript runs a second model (a streaming ASR) alongside PersonaPlex,
which is why 32 GB is the practical minimum:

- PersonaPlex ~19.5 GB + ASR ~2–6 GB fits comfortably on the default **RTX 5090
  (32 GB)** (confirmed running with GPU ASR).
- Controlled by `ENABLE_ASR` at the top of [`serve.py`](./serve.py). Set it to `False`
  and the app runs normally, with the transcript showing only the model's side.

---

## 4. Configuration

There are no command-line flags. Each entry point is configured by a block of
constants at the top of the file — edit them in place and restart:

| File | Controls |
|---|---|
| [`serve.py`](./serve.py) | `PORT`, `CHILD_PORT`, `DEFAULT_REPO` (model pre-loaded at boot), `USE_SSL`, `CPU_OFFLOAD`, `ENABLE_ASR`, `ASR_PORT` |
| [`asr_server.py`](./asr_server.py) | `PORT`, `MODEL`, `CPU_FALLBACK_MODEL`, `DEVICE`, `WINDOW_SECONDS` |
| [`spin_up.py`](./spin_up.py) | which GPU to rent, price ceiling, timeouts, `DESTROY_ON_FAILURE` |

`serve.py` launches `asr_server.py` itself, so **`ASR_PORT` in `serve.py` must match
`PORT` in `asr_server.py`.** Secrets stay out of all of these — they come from the
environment (on the instance) or `.env` (for `spin_up.py`).

---

## 5. Useful commands (on the instance)

```bash
tail -f /workspace/moshi.log     # server logs
tmux attach -t moshi             # attach to the server session
bash /workspace/run_moshi.sh     # restart the server only
```

## 6. Stopping / cleanup

- Easiest: click **Shut down instance** in the UI.
- Or destroy the instance from the vast.ai console (the ■/trash controls).
- Destroying stops all billing; "Stopping" keeps the disk (small storage charge).

---

## Running the tests

No GPU needed — the suite stubs out the model and ASR subprocesses, so it runs on a
laptop in about 30 seconds.

```bash
pip install -r requirements.txt
pytest -q
```

CI ([`.github/workflows/tests.yml`](./.github/workflows/tests.yml)) runs exactly that on
every push to `main` and every PR. Installing the full runtime is deliberate: it doubles
as the check that `requirements.txt` still resolves, so a broken pin fails in CI rather
than halfway through provisioning a rented GPU.

### One requirements file, on purpose

`requirements.txt` carries the test packages next to the runtime ones. Both are pure
Python and constrain none of the runtime pins, so the few MB `provision.sh` adds on the
GPU host buys a single dependency file.

Keep it that way: **a test package that pins a runtime library does not belong here.**
The suite used to need `pytest-aiohttp`, stuck at `<1.1` because 1.1+ requires
`aiohttp>=3.11` while `moshi` forces `aiohttp<3.11` — a test tool held hostage by a
runtime pin, on the file that provisions a paid GPU. Its two fixtures now live in
[`tests/conftest.py`](./tests/conftest.py), built on `aiohttp.test_utils` from aiohttp
itself.

---

## How `provision.sh` works

1. Verifies `HF_TOKEN` is set and activates the image's `/venv/main`.
2. **Egress-tests Hugging Face** (refuses to continue on a firewalled host).
3. **Starts the ~16 GB model download in the background.** It's the long pole of the
   boot and nothing below needs it, so everything else runs underneath it.
4. Installs system deps (`libopus-dev`, `libportaudio2`, `openssl`).
5. Clones the [PersonaPlex repo](https://github.com/NVIDIA/personaplex), then this repo
   (for `serve.py`, `supervisor/`, `models.json` and the client). Both are re-runnable:
   an existing clone is pulled, not re-cloned.
6. Installs `moshi` with `--no-deps`, then adds its other deps — see note below.
7. Installs the app's deps from `requirements.txt` (aiohttp, faster-whisper, … plus the
   test packages — see [Running the tests](#running-the-tests)), followed by
   `nvidia-cublas-cu12` / `nvidia-cudnn-cu12` for faster-whisper's GPU path.
8. Installs Node 20 and builds the web client (`npm install && npm run build`);
   `serve.py` serves the resulting `dist/`.
9. Sanity-checks that torch sees the GPU, then waits for the background download.
10. Kicks off a background prefetch of the *other* models in `models.json`, so switching
    models in the UI doesn't have to download weights first. Detached and non-gating —
    a model whose license you haven't accepted just stays cold.
11. Writes `/workspace/run_moshi.sh` and launches `serve.py` in a `tmux` session on
    `0.0.0.0:8998`.
12. Waits for the model to load, then prints the public URL.

### Note: the Blackwell (RTX 5090) gotcha

`moshi` pins `torch>=2.2,<2.5` (a CUDA 12.1 build), which has **no kernels for
Blackwell GPUs** (RTX 5090, `sm_120`) and fails at runtime with *"no kernel image is
available."* The vast PyTorch image already ships a Blackwell-capable torch (cu128),
so the script installs `moshi` with `--no-deps` to preserve it, then installs the
remaining dependencies explicitly. (On Ada GPUs the default torch is fine, and this is
harmless.)

---

## Tool calling

PersonaPlex / Moshi has **no native tool/function calling** — it's pure speech-in →
speech-out. NVIDIA confirmed this in the
[model discussion](https://huggingface.co/nvidia/personaplex-7b-v1/discussions/2) and
say they may add it in future models.

There is an **experimental** workaround that hangs off the model's *Inner Monologue*
text channel: prompt the model to say a trigger phrase when it needs info, transcribe
the request (e.g. with NVIDIA Parakeet), query a tool/MCP server, then inject the
result back — either by restarting the model with the result in its text prompt, or by
drip-feeding text into the Inner Monologue channel while muting native audio and
playing TTS over it. This is a custom wrapper, not a built-in feature.

---

## Notes

- One model is loaded in VRAM at a time; switching restarts the model server.
- The self-signed cert warning is expected; mic access needs the HTTPS (secure)
  context.
- `VAST_API_KEY` is your vast.ai **account** key (Bearer auth). `spin_up.py` needs it to
  rent at all, and forwards it to the instance so the Shut down button works there.
