"""Minimal stand-in for the real streaming ASR. Serves a /api/transcribe
WebSocket that emits a text frame for each binary (audio) frame it receives.
Usage: python tests/stub_asr.py --port 8997 [--fail]"""
import argparse
import asyncio
import sys
from aiohttp import web


async def transcribe(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    async for msg in ws:
        if msg.type == web.WSMsgType.BINARY:
            await ws.send_str("word")
    return ws


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--fail", action="store_true")
    args = ap.parse_args()
    if args.fail:
        sys.exit(2)
    app = web.Application()
    app.router.add_get("/api/transcribe", transcribe)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", args.port).start()
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
