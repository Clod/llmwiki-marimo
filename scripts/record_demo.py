"""Record the two README demo videos by driving the web interface with Playwright.

    uv run --group web python scripts/record_demo.py short
    uv run --group web python scripts/record_demo.py long --source "<path>/08 FCI de Renta Fija.docx"
    uv run --group web python scripts/record_demo.py short --dry-run
    uv run --group web python scripts/record_demo.py stills

Writes `docs/assets/demo_short.mp4` or `docs/assets/demo_long.mp4`. The upload to
YouTube is manual: this script only produces the file.

**The two videos.**

  * `short` (about 75 s, interface in English, `examples/fairy-tales`): a generated
    page, a cited answer in the pre-retrieval mode, a question the wiki does not
    cover refused before any model call, and the audit of that refusal.
  * `long` (about 5 min, interface in Spanish, `examples/finanzas-argentinas`): the
    picker, the ingestion of one document, the reading screen, the relations, the
    vocabulary, one data file with its tool, the three chat modes, a refusal and its
    audit, the save of a conversation, the lint and repair, and the history.

`stills` writes the README screenshot of the reading screen with two real answers,
`docs/assets/web_read_chat_{en,es}.png`. The screenshot of the history comes from
`tests/web/e2e/capture.py`, whose seeded points need no model.

The application runs in this process over a copy of the example wiki in `WORK`, with
the model of `.env`: the example wiki does not change, and the answers in the videos
are real. `--dry-run` replaces the chat agents, the ingestion, the lint and the draft
of the save with simulated ones: no model call, for checking the script itself.

**Speed.** The chat answers run at real speed: the time of the refusal is the point
of both videos. The operations that wait for the model for minutes (the ingestion,
the verification, the draft of the save, the lint and repair) are sped up. Each
sped-up span has a target length on screen. The post-production computes the
factor from the measured length of the span, and overlays a badge with that factor
("▶▶ ×12") on every frame of the span.

**Toolchain.** Playwright's video does not draw a cursor, so the page gets an
overlay element that follows the mouse. Homebrew's ffmpeg is built without
`drawtext`, so the title cards and the speed badges are rendered as HTML,
screenshotted by the same browser, and placed with the `overlay` filter.

Requires `ffmpeg` on PATH and `uv run playwright install chromium`.
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(_PROJECT_ROOT / "base"), str(_PROJECT_ROOT)]

import uvicorn  # noqa: E402
from playwright.sync_api import Error as PlaywrightError, Page, expect, sync_playwright  # noqa: E402

from web import i18n  # noqa: E402
from web.app import create_app  # noqa: E402
from web.i18n import _  # noqa: E402
from web.settings import WebSettings  # noqa: E402

ASSETS = _PROJECT_ROOT / "docs" / "assets"
WORK = Path("/tmp") / "llmwiki-demo"
VIEWPORT = {"width": 1440, "height": 900}
FPS = 30


@dataclass(frozen=True)
class Video:
    name: str
    wiki: str
    language: str
    cards: tuple[tuple[str, str], tuple[str, str]]


VIDEOS = {
    "short": Video("short", "fairy-tales", "en", (
        ("LLM Wiki", "Your documents, compiled once into a wiki you can read and question. "
                     "Every answer cites the pages it comes from."),
        ("Local-first. Cited.", "github.com/Clod/llmwiki-marimo"))),
    "long": Video("long", "finanzas-argentinas", "es", (
        ("LLM Wiki", "Recorrido completo: ingesta, lectura, datos, chat, mantenimiento e historial."),
        ("Local-first. Con citas.", "github.com/Clod/llmwiki-marimo"))),
}


@dataclass
class Timeline:
    """Wall-clock marks, relative to the start of the recording.

    Each `fast(target)` opens a span that the next `normal()` closes. `target` is the
    length the span gets on screen, in seconds."""

    t0: float = field(default_factory=time.monotonic)
    spans: list[tuple[float, float, float]] = field(default_factory=list)
    _open: tuple[float, float] | None = None

    def now(self) -> float:
        return time.monotonic() - self.t0

    def fast(self, target: float) -> None:
        if self._open is None:
            self._open = (self.now(), target)

    def normal(self) -> None:
        if self._open is not None:
            start, target = self._open
            self.spans.append((start, self.now(), target))
            self._open = None


def speed_of(start: float, end: float, target: float) -> int:
    """The speed-up factor of a span: a whole number, at least 2."""
    return max(2, round((end - start) / target))


# ── the application, in this process ──────────────────────────────────────────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def _serve(video: Video, dry_run: bool):
    """Run the application over a fresh copy of the example wiki; yield its URL."""
    work = WORK / video.name
    shutil.rmtree(work, ignore_errors=True)
    home = work / "home"
    wiki_dir = home / video.wiki
    shutil.copytree(_PROJECT_ROOT / "examples" / video.wiki, wiki_dir, ignore=shutil.ignore_patterns(".git"))
    settings = replace(WebSettings.from_env(), wiki_path=str(wiki_dir), wiki_home=home,
                       recent_file=work / "recent_wikis.json")
    factory = _simulate(wiki_dir, video.language) if dry_run else None
    if not dry_run:
        missing = [name for name, value in (("LLM_BASE_URL", settings.llm_base_url),
                                            ("LLM_API_KEY", settings.llm_api_key),
                                            ("LLM_MODEL", settings.llm_model)) if not value]
        if missing:
            raise SystemExit("the videos show real answers; set in .env: " + ", ".join(missing))
    app = create_app(settings, agents_factory=factory)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise SystemExit("the application did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _simulate(wiki_dir: Path, language: str):
    """Simulated chat agents, ingestion, lint and save draft, as the E2E tests use them."""
    from domain.chat.config import load_config
    from domain.chat import wiki_tools
    from domain.tools.git_ops import auto_commit, init_wiki_repo
    from domain.tools.wiki_fs import create_page
    from services import ingest
    from tests.web.conftest import fake_agents
    from tests.web.e2e.capture import fake_draft
    from tests.web.e2e.server import answer_for

    def fake_ingest(wiki, uploads, client, model, *, full_repair, progress, **_verifier):
        for upload in uploads:
            (wiki.sources_dir / upload.name).write_bytes(upload.contents)
            for line in ("🔍 Validating", "📝 Extracting text", "🧠 Generating the summary page"):
                progress(f"{line} {upload.name}")
                time.sleep(1)
            create_page(wiki.db_path, wiki.path, "/wiki/summaries/", "fci-de-renta-fija", "FCI de Renta Fija",
                        "Los FCI de renta fija invierten en bonos. " * 6, [])
            init_wiki_repo(wiki.path)
            auto_commit(wiki.path, f"ingest: {upload.name}")
        return [SimpleNamespace(status="ingested", doc_id=None) for _ in uploads]

    def fake_lint(wiki, client, model, *, progress, **_verifier):
        for line in ("🔎 Linting 40 pages", "🛠 Repairing 2 problems"):
            progress(line)
            time.sleep(2)
        auto_commit(wiki.path, "lint: repair 2 pages")
        return 2

    ingest.ingest_uploads = fake_ingest
    ingest.lint_and_repair = fake_lint
    wiki_tools.draft_wiki_page = fake_draft
    simulated = fake_agents(answer_for, language=language)
    simulated.agent.delay = 1.0
    simulated.agent.grounded = True
    agents = replace(simulated, config=load_config(wiki_dir))
    return lambda wiki: agents


# ── cursor and captions, injected into every page ─────────────────────────────

_OVERLAY = """
document.addEventListener('DOMContentLoaded', () => {
  const dot = document.createElement('div');
  dot.id = '__demo_cursor';
  dot.style.cssText = `position:fixed;z-index:2147483647;width:22px;height:22px;
    margin:-11px 0 0 -11px;border-radius:50%;pointer-events:none;
    background:rgba(37,99,235,.35);border:2px solid rgba(37,99,235,.9);
    transition:transform .08s linear;left:0;top:0;
    transform:translate(${window.__demoX || -40}px, ${window.__demoY || -40}px)`;
  document.body.appendChild(dot);
  document.addEventListener('mousemove', e => {
    dot.style.transform = `translate(${e.clientX}px, ${e.clientY}px)`;
  }, true);

  const cap = document.createElement('div');
  cap.id = '__demo_caption';
  cap.style.cssText = `position:fixed;z-index:2147483646;left:35%;bottom:56px;
    transform:translateX(-50%);max-width:60%;padding:14px 26px;border-radius:12px;
    background:rgba(17,24,39,.93);color:#fff;font:500 21px/1.4 -apple-system,
    BlinkMacSystemFont,'Segoe UI',sans-serif;text-align:center;opacity:0;
    transition:opacity .35s ease;pointer-events:none;
    box-shadow:0 10px 40px rgba(0,0,0,.35)`;
  document.body.appendChild(cap);
  window.__caption = (text) => {
    cap.textContent = text || '';
    cap.style.opacity = text ? '1' : '0';
  };
});
"""


class Demo:
    """The recorded page, the timeline and the language of the captions."""

    def __init__(self, page: Page, base: str):
        self.page = page
        self.base = base
        self.tl = Timeline()

    def say(self, text: str, hold: float = 0) -> None:
        # A click can start a navigation, or a redirect, that the caller did not wait for.
        for _attempt in range(3):
            try:
                self.page.wait_for_load_state("load")
                self.page.evaluate("t => window.__caption && window.__caption(t)", text)
                break
            except PlaywrightError:
                self.page.wait_for_timeout(500)
        if hold:
            self.page.wait_for_timeout(int(hold * 1000))

    def glide(self, locator, click: bool = True, steps: int = 18) -> None:
        """Move the pointer to an element in visible steps, then click it."""
        locator.scroll_into_view_if_needed()
        box = locator.bounding_box()
        if not box:
            if click:
                locator.click()
            return
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        self.page.mouse.move(x, y, steps=steps)
        self.page.wait_for_timeout(260)
        if click:
            self.page.mouse.click(x, y)

    def tab(self, label: str) -> None:
        self.glide(self.page.locator("nav.tabs").get_by_role("link", name=label, exact=True))
        self.page.wait_for_load_state("networkidle")
        self.page.wait_for_timeout(600)

    def scroll(self, dy: int, pause: float = 1.2) -> None:
        self.page.mouse.wheel(0, dy)
        self.page.wait_for_timeout(int(pause * 1000))

    def mode(self, value: str) -> None:
        select = self.page.locator("#mode")
        self.glide(select, click=False)
        select.select_option(value)
        self.page.wait_for_timeout(700)

    def clear(self) -> None:
        button = self.page.locator("#clear-btn")
        if button.is_enabled():
            self.glide(button)
            expect(self.page.locator("#messages .q")).to_have_count(0)
            self.page.wait_for_timeout(500)

    def ask(self, question: str) -> float:
        """Type the question, send it, wait for the whole answer; return the seconds
        from the send to the end of the answer."""
        box = self.page.locator("#composer textarea")
        self.glide(box)
        box.press_sequentially(question, delay=28)
        self.page.wait_for_timeout(400)
        answers = self.page.locator("#messages .a").count()
        started = time.monotonic()
        box.press("Enter")
        expect(self.page.locator("#messages .a")).to_have_count(answers + 1)
        self.page.wait_for_function(
            "() => !document.querySelector('#messages .typing')"
            " && document.querySelector('#messages .meta:last-of-type')?.textContent.trim()",
            timeout=240_000)
        took = time.monotonic() - started
        text = self.page.locator("#messages .a").last.inner_text()
        if any(m in text for m in ("status_code: 4", "status_code: 5", "Traceback", "failed with exception")):
            raise SystemExit("the chat turn failed; a video of an error is not recorded "
                             "(a provider error can quote a credential back)")
        self.page.wait_for_timeout(600)
        return took

    def ask_refused(self, question: str) -> float:
        """`ask`, for a question the code must refuse before any model call. The turn
        must spend 0 tokens: otherwise the caption of the refusal would be false, and
        the recording stops."""
        took = self.ask(question)
        tokens = self.page.locator("#messages .meta").last.locator(".tokens")
        spent = tokens.inner_text() if tokens.count() else "no token count"
        if not spent.startswith("0 /"):
            raise SystemExit(f"the refusal called the model ({spent}); the caption would be false")
        return took

    def wait_fast(self, target: float, ready, timeout: float = 1800) -> None:
        """Wait for `ready()` inside a sped-up span of `target` seconds on screen."""
        self.tl.fast(target)
        deadline = time.time() + timeout
        while not ready():
            if time.time() > deadline:
                raise SystemExit("an operation did not end in time")
            self.page.wait_for_timeout(500)
        self.tl.normal()

    def log_closed(self) -> bool:
        return "🏁" in (self.page.locator("#progress .log").inner_text() if
                       self.page.locator("#progress .log").count() else "")

    def audit(self) -> None:
        """Open the audit of the last turn in this page, show its path, come back."""
        href = self.page.locator("#audit-buttons a").first.get_attribute("href")
        self.glide(self.page.locator("#audit-buttons a").first, click=False)
        self.page.goto(self.base.rsplit("/w/", 1)[0] + href)
        self.page.wait_for_load_state("networkidle")
        self.page.wait_for_timeout(800)
        select = self.page.locator(".audit-select").last
        if select.count():
            self.glide(select)
            self.page.wait_for_timeout(1500)


# ── the two scripts ───────────────────────────────────────────────────────────

SHORT_COVERED = "What do Cinderella and Snow White have in common?"
SHORT_OFF_CORPUS = "What is the capital of France?"


def drive_short(d: Demo) -> None:
    page = d.page
    page.goto(f"{d.base}/pages/concepts/cinderella")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1200)
    d.say("A model wrote every page of this wiki from three PDF tales.", 3.5)
    d.say("Each page names its sources and links to the related pages.", 0)
    d.scroll(600, 2.5)
    d.scroll(-600, 0.8)
    d.say("")

    d.say("Pre-retrieval mode: the code searches the wiki first, then passes the pages to the model.", 4.0)
    d.say("")
    took = d.ask(SHORT_COVERED)
    d.say(f"Each claim cites the page it comes from. The answer took {took:.0f} s.", 5.0)

    d.tab(_("Read"))                     # the home screen: no page goes with the question
    d.say("A new conversation, from the home screen: the question is about the whole wiki.", 3.5)
    d.clear()
    d.say("Now a question the wiki does not cover.", 2.5)
    d.say("")
    took = d.ask_refused(SHORT_OFF_CORPUS)
    d.say(f"Refused in {took:.1f} s. The question names no topic of the wiki, and the model was never called.", 5.5)

    d.say("The audit shows the path of the question through the checks of the code.", 0)
    d.audit()
    page.wait_for_timeout(3500)
    d.say("")


LONG_DATA_QUESTION = "¿cuánto ganaría con 100000 pesos en cada banco a 30 días?"
LONG_CONCEPT_QUESTION = "¿Qué es una caución bursátil y por qué se la considera de bajo riesgo?"
LONG_OFF_CORPUS = "¿Cuál es la capital de Francia?"


def drive_long(d: Demo, source: Path) -> None:
    page = d.page

    # 1. The picker.
    page.goto(d.base.rsplit("/w/", 1)[0] + "/")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(800)
    d.say("La interfaz web abre cualquier carpeta de wiki de la máquina.", 3.0)
    d.glide(page.locator("a.wiki-name").first)
    page.wait_for_load_state("networkidle")
    d.say("La pantalla de inicio resume la wiki: páginas, fuentes y datos.", 3.5)
    d.say("")

    # 2. The ingestion of one document.
    d.tab(_("Ingest"))
    d.say("Ingestar: se agrega un documento nuevo a la wiki.", 2.5)
    page.locator("input[type=file]").set_input_files(str(source))
    page.wait_for_timeout(1200)
    d.glide(page.get_by_role("button", name=_("Ingest"), exact=True))
    d.say("El modelo escribe el resumen y las páginas de concepto, y las enlaza con las existentes.", 0)
    d.wait_fast(18, d.log_closed)
    d.say("Cada línea del registro es un paso de la ingesta.", 3.0)
    d.say("")

    # 3. The reading screen: the new summary page, its sources, its verification.
    summaries = sorted((WORK / "long" / "home" / "finanzas-argentinas" / "wiki" / "summaries").glob("*.md"),
                       key=lambda p: p.stat().st_mtime)
    slug = summaries[-1].stem
    page.goto(f"{d.base}/pages/summaries/{slug}")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(800)
    d.say("La página de resumen nueva. Al pie, las fuentes de las que salió.", 3.5)
    d.scroll(900, 2.0)
    d.scroll(-900, 0.6)
    verify = page.locator("form").get_by_role("button", name=_("Verify"), exact=True)
    if verify.count() and verify.is_enabled():
        before = page.locator("#verification-state").inner_text()
        d.say("Verificar compara cada oración de la página con el texto de sus fuentes.", 0)
        d.glide(verify)
        d.wait_fast(8, lambda: page.locator("#verification-state").count() > 0
                    and page.locator("#verification-state").inner_text() != before)
        d.say("Las oraciones sin respaldo quedan marcadas. El texto de la página no cambia.", 3.5)
    d.say("")

    # 4. Relations and vocabulary.
    d.tab(pgettext_tab("Relations"))
    page.wait_for_function("document.getElementById('relations')?.dataset.settled === '1'", timeout=30_000)
    d.say("Relaciones: el grafo de las páginas y de los enlaces entre ellas.", 4.5)
    d.tab(_("Vocabulary"))
    d.say("Vocabulario: los nombres que la wiki cubre, sus alias y la lista negra.", 4.5)
    d.say("")

    # 5. One data file with its tool, and a question that uses both.
    d.tab(pgettext_tab("Data"))
    d.say("Datos: archivos con cifras de mercado, separados de las páginas.", 2.5)
    d.glide(page.locator("a[href*='category=plazo_fijo']").first)
    page.wait_for_load_state("networkidle")
    d.say("plazo_fijo.md: la tasa de cada banco, con su fecha y su fuente.", 4.0)
    d.tab(pgettext_tab("Tools"))
    domain = page.locator("details.domain").filter(has_text="finance_argentina").first
    d.glide(domain.locator("> summary"))
    page.wait_for_timeout(800)
    d.say("Herramientas: el chat calcula con funciones de código, no con el modelo.", 3.5)
    d.glide(page.locator("table.requirements").first, click=False)
    d.say("Cada categoría declara las métricas y los atributos que sus datos necesitan.", 4.0)
    d.say("")
    d.tab(_("Read"))                     # back to the reading screen, where the chat is
    d.say("Una pregunta que necesita esos datos.", 2.0)
    d.say("")
    took = d.ask(LONG_DATA_QUESTION)
    d.say(f"El código calculó la tabla con la herramienta de plazo fijo, sobre plazo_fijo.md ({took:.0f} s).", 6.0)
    d.say("")

    # 6. A refusal and its audit; then the three modes with one question.
    d.clear()
    d.say("Una pregunta que la wiki no cubre.", 2.0)
    d.say("")
    reading = page.url
    took = d.ask_refused(LONG_OFF_CORPUS)
    d.say(f"Rechazada en {took:.1f} s: el código decidió que la wiki no la cubre y no llamó al modelo.", 5.0)
    d.say("La auditoría muestra el camino de la pregunta por las comprobaciones.", 0)
    d.audit()
    page.wait_for_timeout(3500)
    d.say("")
    page.goto(reading)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(600)
    for value, caption in (
            ("pre-retrieval", "Recuperación previa: el código busca en la wiki y le pasa las páginas al modelo."),
            ("strict", "Estricto: el modelo consulta la wiki con herramientas. Sin consulta, la respuesta se rechaza."),
            ("streaming", "Sin verificación: la respuesta aparece mientras se genera y nada la comprueba.")):
        d.clear()
        d.mode(value)
        d.say(caption, 3.5)
        d.say("")
        d.ask(LONG_CONCEPT_QUESTION)
        page.wait_for_timeout(2500)

    # 7. The save of the conversation, after the review of the draft.
    d.say("Guardar: el modelo redacta una página con la conversación.", 2.5)
    d.glide(page.locator("#save-btn"))
    title = page.locator("#save-panel input[name=title]")
    d.glide(title)
    title.press_sequentially("Caución bursátil", delay=40)
    d.glide(page.locator("#save-panel .primary"))
    d.wait_fast(5, lambda: page.locator("dialog.review[open]").count() > 0)
    d.say("El borrador se revisa y se puede editar antes de escribir la página.", 4.0)
    d.glide(page.locator("dialog.review").get_by_role("button", name=_("Save to wiki")))
    expect(page.locator("#save-result .ok")).to_be_visible(timeout=60_000)
    page.wait_for_timeout(2500)
    d.say("")

    # 8. Lint and repair.
    d.tab(_("Maintain"))
    d.say("Mantener: revisar y reparar toda la wiki.", 2.5)
    d.glide(page.get_by_role("button", name=_("Run Wiki Lint & Repair")))
    page.wait_for_timeout(1000)
    d.glide(page.locator("#confirm-lint").get_by_role("button", name=_("Review and repair")))
    d.say("El modelo revisa cada página y repara los problemas que encuentra.", 0)
    d.wait_fast(25, d.log_closed)
    d.say("", 2.5)

    # 9. History: back to an earlier point.
    d.tab(_("History"))
    d.say("Historial: cada ingesta, página guardada y reparación es un punto de la wiki.", 4.0)
    point = page.locator("li.commit").nth(1)
    d.glide(point.get_by_role("button", name=_("Go back to this point")))
    expect(point.locator(".confirm")).to_be_visible()
    d.say("La confirmación dice qué páginas cambian antes de volver.", 3.5)
    go_back = point.locator(".confirm").get_by_role("button", name=_("Go back to this point"))
    page.wait_for_function("b => b['htmx-internal-data']?.initHash", arg=go_back.element_handle())
    d.glide(go_back)
    expect(page.locator("#progress .log")).to_contain_text("🏁", timeout=120_000)
    d.say("La wiki volvió a ese punto, y la vuelta es también un punto nuevo.", 4.0)
    d.say("")


def pgettext_tab(text: str) -> str:
    return i18n.pgettext("tab", text)


# ── recording ─────────────────────────────────────────────────────────────────

_CARD = """
<div style="width:{w}px;height:{h}px;display:flex;flex-direction:column;
     align-items:center;justify-content:center;background:#0f172a;color:#fff;
     font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;gap:18px">
  <div style="font-size:56px;font-weight:600;letter-spacing:-.02em">{title}</div>
  <div style="font-size:26px;color:#94a3b8;max-width:70%;text-align:center;
       line-height:1.45">{sub}</div>
</div>
"""

_BADGE = """
<div id="badge" style="display:inline-block;padding:10px 22px;border-radius:999px;
     background:rgba(220,38,38,.92);color:#fff;font:600 30px/1.2 -apple-system,
     BlinkMacSystemFont,'Segoe UI',sans-serif">▶▶ ×{speed}</div>
"""


def record(video: Video, url: str, dry_run: bool, source: Path | None) -> tuple[Path, Timeline]:
    work = WORK / video.name
    raw_dir = work / "raw"
    shutil.rmtree(raw_dir, ignore_errors=True)
    raw_dir.mkdir(parents=True)
    host = url.split("//", 1)[1].split(":", 1)[0]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        # The cards are screenshotted in a context that does not record.
        stills = browser.new_page(viewport=VIEWPORT)
        for name, (title, sub) in zip(("card_open", "card_end"), video.cards):
            stills.set_content(_CARD.format(w=VIEWPORT["width"], h=VIEWPORT["height"], title=title, sub=sub))
            stills.screenshot(path=str(work / f"{name}.png"))
        stills.close()

        ctx = browser.new_context(viewport=VIEWPORT, record_video_dir=str(raw_dir),
                                  record_video_size=VIEWPORT)
        ctx.add_cookies([{"name": i18n.COOKIE, "value": video.language, "domain": host, "path": "/"}])
        ctx.add_init_script(_OVERLAY)
        ctx.set_default_timeout(30_000)
        page = ctx.new_page()
        demo = Demo(page, f"{url}/w/{video.wiki}")
        try:
            if video.name == "short":
                drive_short(demo)
            else:
                drive_long(demo, source)
        except BaseException:
            page.screenshot(path=str(work / "failure.png"))
            raise
        raw = Path(page.video.path())
        ctx.close()

        badges = browser.new_page()
        for start, end, target in demo.tl.spans:
            speed = speed_of(start, end, target)
            badges.set_content(_BADGE.format(speed=speed))
            badges.locator("#badge").screenshot(path=str(work / f"badge_{speed}.png"), omit_background=True)
        badges.close()
        browser.close()
    return raw, demo.tl


# ── post-production ───────────────────────────────────────────────────────────

def _ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def build(video: Video, raw: Path, tl: Timeline, out: Path) -> None:
    """Speed up the marked spans with their badge, bookend with the cards, write an mp4."""
    work = WORK / video.name
    parts: list[Path] = []

    def still(name: str) -> None:
        seg = work / f"seg_{name}.mp4"
        _ffmpeg(["-loop", "1", "-i", str(work / f"{name}.png"), "-t", "3", "-r", str(FPS),
                 "-vf", "format=yuv420p", str(seg)])
        parts.append(seg)

    still("card_open")
    cuts: list[tuple[float, float, int]] = []          # (start, end, speed); end -1 = to the end
    cursor = 0.0
    for start, end, target in tl.spans:
        if start > cursor:
            cuts.append((cursor, start, 1))
        cuts.append((start, end, speed_of(start, end, target)))
        cursor = end
    cuts.append((cursor, -1.0, 1))
    for i, (start, end, speed) in enumerate(cuts):
        if end >= 0 and end - start < 0.35:
            continue
        seg = work / f"seg_{i:02d}.mp4"
        args = ["-ss", f"{start:.3f}"] + (["-to", f"{end:.3f}"] if end >= 0 else []) + ["-i", str(raw)]
        if speed == 1:
            args += ["-vf", "format=yuv420p"]
        else:
            args += ["-loop", "1", "-i", str(work / f"badge_{speed}.png"), "-filter_complex",
                     f"[0:v]setpts=PTS/{speed}[v];[v][1:v]overlay=W-w-36:84:shortest=1,format=yuv420p"]
        _ffmpeg(args + ["-an", "-r", str(FPS), str(seg)])
        parts.append(seg)
    still("card_end")

    listing = work / "concat.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in parts))
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing), "-c:v", "libx264", "-preset", "slow",
             "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)])


# ── the README screenshot of the reading screen ──────────────────────────────

STILLS = Video("stills", "finanzas-argentinas", "es", (("", ""), ("", "")))
# The data question goes last: asked first, its table (computed by the code, not by a
# tool call of the model) makes the model doubt it in the next answer.
STILL_QUESTIONS = ("¿Qué es un plazo fijo UVA?", "¿Qué tasa de plazo fijo ofrece el Banco Galicia?")


def capture_stills(url: str, out_dir: Path) -> None:
    """`web_read_chat_{en,es}.png`: the reading screen with two real answers, in each
    language of the interface. The questions are in Spanish, the language of the wiki."""
    host = url.split("//", 1)[1].split(":", 1)[0]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for lang in ("en", "es"):
            ctx = browser.new_context(viewport=VIEWPORT)
            ctx.add_cookies([{"name": i18n.COOKIE, "value": lang, "domain": host, "path": "/"}])
            ctx.set_default_timeout(30_000)
            page = ctx.new_page()
            page.goto(f"{url}/w/{STILLS.wiki}/pages/concepts/plazo-fijo-uva")
            page.wait_for_load_state("networkidle")
            page.locator("#index-btn").click()          # wider than 1100 px the index column opens
            demo = Demo(page, f"{url}/w/{STILLS.wiki}")
            for question in STILL_QUESTIONS:
                demo.ask(question)
            page.mouse.move(VIEWPORT["width"] // 2, VIEWPORT["height"] - 20)
            page.evaluate("window.scrollTo(0, 0); document.getElementById('messages').scrollTop = 0")
            page.wait_for_timeout(600)
            page.screenshot(path=str(out_dir / f"web_read_chat_{lang}.png"))
            ctx.close()
        browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("video", choices=sorted(VIDEOS) + ["stills"])
    parser.add_argument("--source", type=Path, help="the document the long video ingests")
    parser.add_argument("--dry-run", action="store_true", help="simulated model: checks the script")
    args = parser.parse_args()
    if args.video == "stills":
        out_dir = WORK / "stills" if args.dry_run else ASSETS
        with _serve(STILLS, args.dry_run) as url:
            capture_stills(url, out_dir)
        print(f"wrote {out_dir}/web_read_chat_en.png and web_read_chat_es.png")
        return
    video = VIDEOS[args.video]
    if video.name == "long" and not (args.source and args.source.is_file()):
        raise SystemExit("the long video needs --source, a document to ingest")
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is not on PATH")
    i18n.set_language(video.language)   # the labels the script clicks, in the language of the interface

    with _serve(video, args.dry_run) as url:
        print(f"recording the {video.name} video…")
        raw, tl = record(video, url, args.dry_run, args.source)
    work = WORK / video.name
    (work / "timeline.json").write_text(json.dumps({"spans": tl.spans}, indent=2))
    out = (work if args.dry_run else ASSETS) / f"demo_{video.name}.mp4"
    print(f"post-producing ({len(tl.spans)} sped-up span(s))…")
    build(video, raw, tl, out)
    duration = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                               "-of", "default=nw=1:nk=1", str(out)], capture_output=True, text=True).stdout
    print(f"wrote {out} — {float(duration):.1f} s, {out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
