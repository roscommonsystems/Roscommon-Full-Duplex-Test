#!/usr/bin/env python3
"""asr_server.py — user-speech transcription server for the "You:" transcript.

Serves a /api/transcribe WebSocket that receives raw 16 kHz mono float32 PCM
frames (the client captures + downsamples its mic) and transcribes fixed ~2.5s
windows with faster-whisper, emitting recognized text as WS text frames.

No opus/sphn decoding — the client sends PCM directly, which is deterministic
and avoids native-decoder threading issues. faster-whisper (CTranslate2) is
isolated from moshi's torch build and falls back to CPU if CUDA isn't usable.

Usage: python asr_server.py --port 8997 [--model base.en] [--device cuda]
"""
import argparse
import asyncio

import numpy as np
from aiohttp import web
from faster_whisper import WhisperModel

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
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8997)
    ap.add_argument("--model", default="base.en")
    ap.add_argument("--cpu-model", default="small.en", help="fallback model if CUDA fails")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--window-sec", type=float, default=2.5)
    args = ap.parse_args()

    model = load_model(args.model, args.device, args.cpu_model)
    loop = asyncio.get_running_loop()
    window = int(args.window_sec * ASR_RATE)

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
    await web.TCPSite(runner, "127.0.0.1", args.port).start()
    print(f"ASR server ready on 127.0.0.1:{args.port}", flush=True)
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
