#!/usr/bin/env python3
"""asr_server.py — user-speech transcription server for the "You:" transcript.

Serves a /api/transcribe WebSocket that receives the SAME opus audio frames the
client sends to moshi, decodes them to PCM with sphn (like moshi's server), and
transcribes fixed ~2.5s windows with faster-whisper, emitting recognized text
as WS text frames.

faster-whisper (CTranslate2) is isolated from moshi's torch build, so it cannot
break the GPU; it falls back to CPU if CUDA isn't usable. Decoding runs on the
recv path; transcription runs in a background task so a slow transcribe never
starves the opus decoder.

Usage: python asr_server.py --port 8997 [--model base.en] [--device cuda]
"""
import argparse
import asyncio

import numpy as np
import sphn
from aiohttp import web
from faster_whisper import WhisperModel

SAMPLE_RATE = 24000  # Mimi / opus-reader PCM rate (matches moshi)
ASR_RATE = 16000     # faster-whisper expects 16 kHz mono float32


def load_model(model_name, device):
    attempts = [(device, "int8_float16"), ("cpu", "int8")] if device != "cpu" else [("cpu", "int8")]
    last = None
    for dev, ctype in attempts:
        try:
            m = WhisperModel(model_name, device=dev, compute_type=ctype)
            list(m.transcribe(np.zeros(ASR_RATE, dtype=np.float32), language="en")[0])
            print(f"ASR using device={dev} model={model_name}", flush=True)
            return m
        except Exception as e:  # noqa: BLE001
            print(f"ASR device {dev} unavailable: {e}", flush=True)
            last = e
    raise RuntimeError(f"no working ASR device: {last}")


def _resample_24k_to_16k(x):
    n_out = int(len(x) * ASR_RATE / SAMPLE_RATE)
    if n_out <= 0:
        return np.zeros(0, dtype=np.float32)
    idx = np.linspace(0, len(x) - 1, n_out)
    return np.interp(idx, np.arange(len(x)), x).astype(np.float32)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8997)
    ap.add_argument("--model", default="base.en")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--window-sec", type=float, default=2.5)
    args = ap.parse_args()

    model = load_model(args.model, args.device)
    loop = asyncio.get_running_loop()
    window = int(args.window_sec * SAMPLE_RATE)

    def transcribe_pcm(pcm24):
        pcm16 = _resample_24k_to_16k(pcm24)
        segments, _ = model.transcribe(pcm16, language="en", beam_size=1)
        return " ".join(s.text.strip() for s in segments).strip()

    async def handle(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        reader = sphn.OpusStreamReader(SAMPLE_RATE)
        chunks = []          # decoded PCM pieces awaiting transcription

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
                if msg.type != web.WSMsgType.BINARY:
                    continue
                # sphn API (this version): feed with append_bytes, then drain
                # decoded PCM with read_pcm(). Draining keeps the decoder alive.
                try:
                    reader.append_bytes(bytes(msg.data))
                    pcm = reader.read_pcm()
                except Exception as e:  # noqa: BLE001
                    print(f"opus decode error: {e}", flush=True)
                    continue
                if pcm is not None and len(pcm):
                    chunks.append(np.asarray(pcm, dtype=np.float32))
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
