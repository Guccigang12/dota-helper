
from __future__ import annotations

import asyncio
import threading
import traceback
from typing import Any, Awaitable, Callable

from PyQt6.QtCore import QThread, pyqtSignal


class AsyncWorker(QThread):
                                                          

    task_failed = pyqtSignal(str, str)                               

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()

                                                                          

    def run(self) -> None:                                        
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            self._loop.close()
            self._loop = None

                                                                          

    def wait_ready(self, timeout: float = 10.0) -> bool:
        return self._ready.wait(timeout)

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            raise RuntimeError("Event loop ещё не запущен")
        return self._loop

    def submit(
        self,
        name: str,
        coro: Awaitable[Any],
        done_callback: Callable[[Any], None] | None = None,
    ) -> asyncio.Future:




           
        if self._loop is None or not self.isRunning():
            raise RuntimeError("AsyncWorker не запущен")

        future = asyncio.run_coroutine_threadsafe(coro, self._loop)

        def _on_done(f: asyncio.Future) -> None:
                                                                         
            if f.cancelled():
                return
            try:
                exc = f.exception()
            except Exception:                                        
                return
            if exc is not None:
                traceback.print_exception(type(exc), exc, exc.__traceback__)
                self.task_failed.emit(name, f"{type(exc).__name__}: {exc}")
            elif done_callback is not None:
                try:
                    done_callback(f.result())
                except Exception:                                                 
                    traceback.print_exc()

        future.add_done_callback(_on_done)
        return future

    def stop(self) -> None:
        if self._loop is not None and self.isRunning():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self.wait(5000)
