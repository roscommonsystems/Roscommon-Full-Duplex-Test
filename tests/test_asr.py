import sys
import pytest
from supervisor.asr import AsrChild

STUB = "tests/stub_asr.py"


def ok_cmd(port):
    return [sys.executable, STUB, "--port", str(port)]


async def test_start_reaches_ready():
    asr = AsrChild(ok_cmd(8971), port=8971, ready_timeout=15)
    try:
        await asr.start()
        assert asr.state == "ready"
        assert asr.available is True
    finally:
        await asr.aclose()


async def test_failed_start_sets_error():
    asr = AsrChild([sys.executable, STUB, "--port", "8972", "--fail"], port=8972, ready_timeout=5)
    try:
        await asr.start()
        assert asr.state == "error"
        assert asr.available is False
        assert asr.error
    finally:
        await asr.aclose()
