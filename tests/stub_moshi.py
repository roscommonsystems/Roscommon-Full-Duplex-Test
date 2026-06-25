"""Minimal stand-in for `moshi.server` used in tests.

Usage: python tests/stub_moshi.py --port 8999 [--delay 0] [--fail]
Serves a /api/chat WebSocket that sends one 'handshake' text frame, then
echoes binary frames. With --fail it exits non-zero without serving.
"""
import argparse
import asyncio
import sys
from aiohttp import web


async def chat(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    await ws.send_str("handshake")
    async for msg in ws:
        if msg.type == web.WSMsgType.BINARY:
            await ws.send_bytes(msg.data)
    return ws


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--delay", type=float, default=0.0)  # seconds before binding
    ap.add_argument("--fail", action="store_true")
    args = ap.parse_args()
    if args.fail:
        sys.exit(2)
    await asyncio.sleep(args.delay)
    app = web.Application()
    app.router.add_get("/api/chat", chat)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", args.port)
    await site.start()
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
