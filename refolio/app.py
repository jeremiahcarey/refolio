"""Desktop shell: the same local server as `refolio`, shown in a native window instead of a browser.

This is what the Briefcase installers launch. Nothing here is needed for the command line.
"""
from __future__ import annotations

import asyncio
import socket
import threading
import time
from pathlib import Path

import toga

from . import __version__
from .cli import _free_port


class Refolio(toga.App):
    def startup(self):
        import uvicorn

        from .server import create_app

        self._loop = asyncio.get_running_loop()
        port = _free_port(8765)
        self._server = uvicorn.Server(uvicorn.Config(
            create_app(save_file=self._save_file), host="127.0.0.1", port=port, log_level="warning"))
        threading.Thread(target=self._server.run, daemon=True).start()
        self._wait_for(port)

        self.main_window = toga.MainWindow(title=self.formal_name, size=(1280, 860))
        self.main_window.content = toga.WebView(url=f"http://127.0.0.1:{port}/", style=toga.style.Pack(flex=1))
        self.main_window.show()

    @staticmethod
    def _wait_for(port: int, timeout: float = 15):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    return
            time.sleep(0.05)

    def _save_file(self, name: str, data: bytes) -> str | None:
        """Called from a server thread: show a save dialog on the UI thread and wait for it."""
        async def ask():
            return await self.main_window.dialog(toga.SaveFileDialog("Save EPUB", suggested_filename=name,
                                                                     file_types=["epub"]))

        chosen = asyncio.run_coroutine_threadsafe(ask(), self._loop).result()
        if not chosen:
            return None
        Path(chosen).write_bytes(data)
        return str(chosen)

    def on_exit(self):
        self._server.should_exit = True
        return True


def main() -> Refolio:
    return Refolio("Refolio", "com.jeremiahcarey.refolio", version=__version__,
                   home_page="https://github.com/jeremiahcarey/refolio")
