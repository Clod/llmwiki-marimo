"""Start the LLM Wiki web interface and open it in the browser.

    uv run --group web python scripts/open_web.py [--port 8765]

The `open_web` shortcut that the installer writes runs this script. When the port
already answers (the interface is running), it only opens the browser. Otherwise it
starts the server and opens the browser as soon as the server answers. Stop the
server with Ctrl+C or by closing the window.
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_PORT = 8765


def answers(port: int, host: str = "127.0.0.1") -> bool:
    """Whether something listens on `host:port`."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def open_when_ready(url: str, port: int, *, timeout: float = 60.0, open_url=webbrowser.open,
                    is_up=answers, sleep=time.sleep) -> bool:
    """Open `url` once the server answers on `port`; give up after `timeout` seconds."""
    waited = 0.0
    while waited < timeout:
        if is_up(port):
            open_url(url)
            return True
        sleep(0.25)
        waited += 0.25
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start the LLM Wiki web interface and open the browser.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    url = f"http://localhost:{args.port}/"
    if answers(args.port):
        print(f"The web interface is already running: opening {url}")
        webbrowser.open(url)
        return 0
    import uvicorn

    threading.Thread(target=open_when_ready, args=(url, args.port), daemon=True).start()
    print(f"Starting the web interface at {url} (Ctrl+C or close this window to stop it).")
    uvicorn.run("web.app:app", host="127.0.0.1", port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
