import asyncio
import aiohttp


class AsrChild:
    """Owns a single persistent ASR server subprocess (model-agnostic)."""

    def __init__(self, command, port=8997, ready_timeout=900.0):
        self._command = list(command)
        self.port = port
        self.ready_timeout = ready_timeout
        self.state = "loading"
        self.error = None
        self._proc = None
        self._session = None

    async def _ensure_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _wait_ready(self):
        # Any HTTP response from the child means its server is up (the ASR binds
        # its port after the model loads). We do not probe the WS endpoint.
        session = await self._ensure_session()
        url = f"http://127.0.0.1:{self.port}/"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.ready_timeout
        while loop.time() < deadline:
            if self._proc and self._proc.returncode is not None:
                raise RuntimeError(f"asr exited early (code {self._proc.returncode})")
            try:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=5), allow_redirects=False
                ) as resp:
                    await resp.read()
                    return
            except Exception:
                await asyncio.sleep(0.5)
        raise TimeoutError("asr did not become ready in time")

    async def start(self):
        self.state = "loading"
        self.error = None
        try:
            self._proc = await asyncio.create_subprocess_exec(*self._command)
            await self._wait_ready()
            self.state = "ready"
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
            self.state = "error"
            await self.stop()

    @property
    def available(self):
        return self.state == "ready"

    async def stop(self):
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=20)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        self._proc = None

    async def aclose(self):
        await self.stop()
        if self._session and not self._session.closed:
            await self._session.close()
