import asyncio
import json
from pathlib import Path
import sys
import time


class BookReader:
    def __init__(self):
        self._proc: asyncio.subprocess.Process | None = None
        self._latest: dict | None = None
        self._last_update_time: float | None = None
        self._token_ids: list[str] | None = None
        self._stale_book_s = 3.0
        self._backoff_s = 1.0
        self._restart_lock = asyncio.Lock()

    async def start(self, token_ids: list[str]) -> None:
        if len(token_ids) < 2:
            raise ValueError("token_ids must contain 2 ids")

        self._token_ids = [str(token_ids[0]), str(token_ids[1])]
        await self._spawn()
        asyncio.create_task(self._stale_watchdog())

    async def stop(self) -> None:
        proc = self._proc
        self._proc = None
        self._latest = None
        self._last_update_time = None
        if proc and proc.returncode is None:
            proc.kill()
            try:
                await proc.communicate()
            except Exception:
                pass

    def latest(self) -> dict | None:
        return self._latest

    async def _spawn(self) -> None:
        assert self._token_ids is not None
        trade_engine_dir = Path(__file__).resolve().parents[1] / "trade-engine"

        token_ids_json = json.dumps(self._token_ids)
        self._proc = await asyncio.create_subprocess_exec(
            "bun",
            "run",
            "scripts/py_bridge_book.ts",
            "--token-ids",
            token_ids_json,
            cwd=str(trade_engine_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        asyncio.create_task(self._read_stdout(self._proc))
        asyncio.create_task(self._read_stderr(self._proc))
        asyncio.create_task(self._watch_exit(self._proc))

    async def _read_stdout(self) -> None:
        raise RuntimeError("use _read_stdout(proc)")

    async def _read_stdout(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        while True:
            line_b = await proc.stdout.readline()
            if not line_b:
                return
            line = line_b.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                print(
                    f"[{time.strftime('%H:%M:%S')}] BOOK BAD JSON: {line[:200]}",
                    file=sys.stderr,
                    flush=True,
                )
                continue

            self._latest = obj
            self._last_update_time = time.time()

    async def _read_stderr(self) -> None:
        raise RuntimeError("use _read_stderr(proc)")

    async def _read_stderr(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr is not None
        while True:
            line_b = await proc.stderr.readline()
            if not line_b:
                return
            line = line_b.decode("utf-8", errors="replace").rstrip()
            if line:
                print(line, file=sys.stderr, flush=True)

    async def _restart(self, reason: str) -> None:
        async with self._restart_lock:
            # Another restart may have already replaced the process.
            proc = self._proc
            if proc and proc.returncode is None:
                print(
                    f"[{time.strftime('%H:%M:%S')}] BOOK RESTART: {reason}",
                    file=sys.stderr,
                    flush=True,
                )
                proc.kill()
                try:
                    await proc.communicate()
                except Exception:
                    pass

            await asyncio.sleep(self._backoff_s)
            self._backoff_s = min(10.0, 5.0 if self._backoff_s < 5.0 else 10.0)
            await self._spawn()

    async def _watch_exit(self, proc: asyncio.subprocess.Process) -> None:
        code = await proc.wait()
        # If this is not the current process, ignore.
        if proc is not self._proc:
            return
        await self._restart(f"wrapper exited code={code}")

    async def _stale_watchdog(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            if not self._proc or self._proc.returncode is not None:
                continue
            if self._last_update_time is None:
                continue
            if time.time() - self._last_update_time > self._stale_book_s:
                await self._restart("stale book")

