"""The application on a free port over a copy of `examples/finanzas-argentinas`,
with simulated agents. Used by the Playwright tests and by `capture.py`."""

from __future__ import annotations

import shutil
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

import uvicorn

from domain.chat.config import load_config
from tests.web.conftest import fake_agents
from web.app import create_app
from web.settings import WebSettings

EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "finanzas-argentinas"
WIKI_ID = "finanzas-argentinas"


@dataclass
class Live:
    url: str
    app: object
    wiki_dir: Path
    agents: object

    def wiki(self):
        return self.app.state.web.get_wiki(WIKI_ID).wiki


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def answer_for(prompt: str) -> str:
    """The simulated model: each question gets a recognizable answer."""
    prompt = prompt.rstrip().rsplit("\n\n", 1)[-1]   # the question closes every prompt
    if "plazo fijo UVA" in prompt:
        return "Un plazo fijo UVA se ajusta por inflación. Referencia: wiki/concepts/plazo-fijo-uva.md"
    if "Banco Galicia" in prompt:
        return "| plazo | tasa |\n|---|---|\n| 30 días | 32.5% |\n| 365 días | 36.5% |"
    if "caución" in prompt:
        return "Una caución bursátil es un préstamo garantizado con títulos."
    if "cuánto rinde" in prompt:
        return "Rinde según la tasa vigente."
    return "Respuesta simulada."


@contextmanager
def serve(tmp_path: Path) -> Iterator[Live]:
    home = tmp_path / "home"
    home.mkdir()
    wiki_dir = home / WIKI_ID
    shutil.copytree(EXAMPLE, wiki_dir)
    settings = WebSettings(
        wiki_path=str(wiki_dir), wiki_home=home, recent_file=tmp_path / "recent_wikis.json",
        llm_base_url="http://llm.invalid/v1", llm_api_key="key", llm_model="model",
        ingest_base_url="http://llm.invalid/v1", ingest_api_key="key", ingest_model="model")
    simulated = fake_agents(answer_for, language="es")
    simulated.agent.delay = 1.0
    simulated.agent.grounded = True
    # The simulated agents, with the wiki's real chat configuration: the refusals
    # (off-limits topics, scope) are the code's, and need it.
    agents = replace(simulated, config=load_config(wiki_dir))
    app = create_app(settings, agents_factory=lambda wiki: agents)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "the server did not start"
    try:
        yield Live(f"http://127.0.0.1:{port}", app, wiki_dir, agents)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
