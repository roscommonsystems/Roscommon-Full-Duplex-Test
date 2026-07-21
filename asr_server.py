#!/usr/bin/env python3
"""asr_server.py — user-speech transcription server for the "You:" transcript.

Serves a /api/transcribe WebSocket that receives raw 16 kHz mono float32 PCM
frames (the client captures + downsamples its mic) and transcribes fixed ~2.5s
windows with faster-whisper, emitting recognized text as WS text frames.

No opus/sphn decoding — the client sends PCM directly, which is deterministic
and avoids native-decoder threading issues. faster-whisper (CTranslate2) is
isolated from moshi's torch build and falls back to CPU if CUDA isn't usable.

Usage: python asr_server.py   (configuration lives in the constants below)
"""
import asyncio

import numpy as np
from aiohttp import web
from faster_whisper import WhisperModel

# ============================ Configuration ============================
# Edit in place. serve.py launches this as a child process, so PORT must
# stay in sync with ASR_PORT in serve.py.
PORT = 8997
MODEL = "medium.en"              # GPU model — accurate and low-latency
CPU_FALLBACK_MODEL = "small.en"  # smaller, so CPU can still keep up
DEVICE = "cuda"                  # "cpu" skips the GPU attempt entirely
WINDOW_SECONDS = 2.5             # audio accumulated per transcription pass
# =======================================================================

ASR_RATE = 16000  # client sends 16 kHz mono float32


def load_model(model_name, device, cpu_model):
    # Prefer the requested model on GPU (accurate + low latency); if CUDA isn't
    # usable, fall back to a SMALLER model on CPU so it can still keep up.
    attempts = []
    if device != "cpu":
        attempts.append((model_name, device, "float16"))
    attempts.append((cpu_model, "cpu", "int8"))
    last = None
    for mname, dev, ctype in attempts:
        try:
            m = WhisperModel(mname, device=dev, compute_type=ctype)
            list(m.transcribe(np.zeros(ASR_RATE, dtype=np.float32), language="en")[0])
            print(f"ASR using device={dev} model={mname}", flush=True)
            return m
        except Exception as e:  # noqa: BLE001
            print(f"ASR {dev}/{mname} unavailable: {e}", flush=True)
            last = e
    raise RuntimeError(f"no working ASR device: {last}")


async def main():
    model = load_model(MODEL, DEVICE, CPU_FALLBACK_MODEL)
    loop = asyncio.get_running_loop()
    window = int(WINDOW_SECONDS * ASR_RATE)

    def transcribe_pcm(pcm16):
        # vad_filter drops silence (Whisper hallucinates "thank you"/"..." on it);
        # condition_on_previous_text=False stops cross-window repetition loops.
        segments, _ = model.transcribe(
            pcm16,
            language="en",
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        return " ".join(s.text.strip() for s in segments).strip()

    async def handle(request):
        ws = web.WebSocketResponse(max_msg_size=0)
        await ws.prepare(request)
        chunks = []  # incoming PCM pieces awaiting transcription

        async def transcriber():
            acc = np.zeros(0, dtype=np.float32)
            while not ws.closed:
                await asyncio.sleep(0.25)
                if chunks:
                    acc = np.concatenate([acc] + chunks)
                    chunks.clear()
                while len(acc) >= window:
                    piece, acc = acc[:window], acc[window:]
                    try:
                        text = await loop.run_in_executor(None, transcribe_pcm, piece)
                    except Exception as e:  # noqa: BLE001
                        print(f"transcribe error: {e}", flush=True)
                        continue
                    if text and not ws.closed:
                        await ws.send_str(text)

        task = asyncio.ensure_future(transcriber())
        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.BINARY:
                    pcm = np.frombuffer(msg.data, dtype=np.float32)
                    if len(pcm):
                        chunks.append(pcm.copy())
        finally:
            task.cancel()
        return ws

    app = web.Application()
    app.router.add_get("/api/transcribe", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", PORT).start()
    print(f"ASR server ready on 127.0.0.1:{PORT}", flush=True)
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
