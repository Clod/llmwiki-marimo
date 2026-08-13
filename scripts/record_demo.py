"""Record the README demo video by driving the read app with Playwright.

    uv run python scripts/record_demo.py

Writes `docs/assets/demo.mp4` (and leaves the raw capture beside it for
inspection). Upload is manual — this only produces the file.

**Why this lives in the repo.** The previous demo was recorded by a rig in
`/tmp`, which is exactly where it went. The interface changed and the video
outlived its accuracy with no way to regenerate it. One command, in version
control, is the fix.

**What it shows**, in ~70 seconds, chosen because it is what the tabbed
interface can demonstrate and the old three-column one could not:

  1. the **Read** tab — generated pages, and the See-also links between them
  2. the **Chat** tab — both mode checkboxes, side by side
  3. **Strict mode**: a real cross-document answer, every claim citing its page
  4. the **Save to wiki** form — the agent has no write tool, you do
  5. **Pre-retrieval**: the same off-corpus question refused *instantly*,
     because code decided the wiki does not cover it and never called the model

Step 5 is the point of the whole video. The refusal is not faster because the
model is quick — it is faster because there is no model call at all, and that is
visible in the recording as an answer with no wait in front of it.

**Two things about the toolchain**, both learned the hard way and both still
true:

  * Playwright's video does not draw a cursor, so one is injected as an overlay
    element that follows the mouse. Without it the video looks like the app is
    operating itself.
  * Homebrew's ffmpeg is built without `drawtext`, so title cards cannot be
    burned in. They are rendered as HTML and screenshotted by the same browser.

Requires `ffmpeg` on PATH and `uv run playwright install chromium`.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import Page, async_playwright

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "base"))

from config import require_llm_config, settings  # noqa: E402

WIKI = _PROJECT_ROOT / "examples" / "fairy-tales"
ASSETS = _PROJECT_ROOT / "docs" / "assets"
WORK = Path("/tmp") / "llmwiki-demo"

VIEWPORT = {"width": 1440, "height": 900}
FPS = 30

COVERED_QUESTION = "What do Cinderella and Snow White have in common?"
OFF_CORPUS_QUESTION = "What is the capital of France?"

# Waiting for a model is dead air. Marked spans are sped up rather than cut, so
# the viewer still sees that the work took time.
SPEEDUP = 6.0


@dataclass
class Timeline:
    """Wall-clock marks, relative to the start of the recording.

    ffmpeg needs spans, not events, so each `fast()` opens a span that the next
    `normal()` closes.
    """

    t0: float = field(default_factory=time.monotonic)
    spans: list[tuple[float, float]] = field(default_factory=list)
    _open: float | None = None

    def now(self) -> float:
        return time.monotonic() - self.t0

    def fast(self) -> None:
        if self._open is None:
            self._open = self.now()

    def normal(self) -> None:
        if self._open is not None:
            self.spans.append((self._open, self.now()))
            self._open = None


# ── the app under test ────────────────────────────────────────────────────────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 60) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("localhost", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


# ── cursor + captions, injected into the page ─────────────────────────────────

_OVERLAY = """
(() => {
  const dot = document.createElement('div');
  dot.id = '__demo_cursor';
  dot.style.cssText = `position:fixed;z-index:2147483647;width:22px;height:22px;
    margin:-11px 0 0 -11px;border-radius:50%;pointer-events:none;
    background:rgba(37,99,235,.35);border:2px solid rgba(37,99,235,.9);
    transition:transform .08s linear;left:0;top:0`;
  document.body.appendChild(dot);
  document.addEventListener('mousemove', e => {
    dot.style.transform = `translate(${e.clientX}px, ${e.clientY}px)`;
  }, true);

  const cap = document.createElement('div');
  cap.id = '__demo_caption';
  cap.style.cssText = `position:fixed;z-index:2147483646;left:50%;bottom:38px;
    transform:translateX(-50%);max-width:78%;padding:14px 26px;border-radius:12px;
    background:rgba(17,24,39,.93);color:#fff;font:500 21px/1.4 -apple-system,
    BlinkMacSystemFont,'Segoe UI',sans-serif;text-align:center;opacity:0;
    transition:opacity .35s ease;pointer-events:none;
    box-shadow:0 10px 40px rgba(0,0,0,.35)`;
  document.body.appendChild(cap);
  window.__caption = (text) => {
    cap.textContent = text || '';
    cap.style.opacity = text ? '1' : '0';
  };
})();
"""


async def _say(page: Page, text: str, hold: float = 0) -> None:
    await page.evaluate("t => window.__caption && window.__caption(t)", text)
    if hold:
        await page.wait_for_timeout(int(hold * 1000))


async def _glide(page: Page, locator, steps: int = 18) -> None:
    """Move the pointer to an element in visible steps, then click.

    A teleporting cursor reads as a glitch; the eye needs the travel to believe
    something was clicked.
    """
    box = await locator.bounding_box()
    if not box:
        await locator.click()
        return
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    await page.mouse.move(x, y, steps=steps)
    await page.wait_for_timeout(260)
    await page.mouse.click(x, y)


# ── chat plumbing (same shape as the E2E suite) ───────────────────────────────

async def _transcript(page: Page) -> str:
    return await page.locator("marimo-chatbot").last.evaluate(
        """el => { const walk = n => n.shadowRoot
             ? Array.from(n.shadowRoot.childNodes).map(walk).join('')
             : n.nodeType === Node.TEXT_NODE ? n.textContent
             : n.childNodes ? Array.from(n.childNodes).map(walk).join('') : '';
           return walk(el); }"""
    )


async def _ask(page: Page, question: str) -> None:
    box = page.locator("marimo-chatbot [contenteditable='true']").last
    await _glide(page, box)
    await box.type(question, delay=28)          # visible typing
    await page.wait_for_timeout(400)
    await _glide(page, page.locator("marimo-chatbot button[type='submit']").last)


async def _await_answer(page: Page, tl: Timeline, timeout: float = 120.0) -> float:
    """Wait for the turn, marking the wait as fast-forwardable. Returns seconds."""
    started = tl.now()
    tl.fast()
    deadline = time.time() + timeout
    last, stable = "", 0
    while time.time() < deadline:
        await page.wait_for_timeout(400)
        current = await _transcript(page)
        if "Stop" not in current and current == last:
            stable += 1
            if stable >= 2:
                break
        else:
            stable = 0
        last = current
    tl.normal()
    took = tl.now() - started
    if any(m in last for m in ("failed with exception", "status_code: 4", "status_code: 5")):
        raise SystemExit(
            "the chat turn failed — refusing to record a video of an error "
            "(provider errors can quote credentials back). Fix the model config "
            "and re-run."
        )
    return took


# ── title cards, rendered as HTML because ffmpeg here has no drawtext ─────────

_CARD = """
<div style="width:{w}px;height:{h}px;display:flex;flex-direction:column;
     align-items:center;justify-content:center;background:#0f172a;color:#fff;
     font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;gap:18px">
  <div style="font-size:56px;font-weight:600;letter-spacing:-.02em">{title}</div>
  <div style="font-size:26px;color:#94a3b8;max-width:70%;text-align:center;
       line-height:1.45">{sub}</div>
</div>
"""


async def _card(page: Page, out: Path, title: str, sub: str) -> None:
    await page.set_content(_CARD.format(w=VIEWPORT["width"], h=VIEWPORT["height"],
                                        title=title, sub=sub))
    await page.wait_for_timeout(300)
    await page.screenshot(path=str(out))


# ── the demo itself ───────────────────────────────────────────────────────────

async def _drive(url: str) -> tuple[Path, Timeline]:
    WORK.mkdir(parents=True, exist_ok=True)
    video_dir = WORK / "raw"
    shutil.rmtree(video_dir, ignore_errors=True)
    video_dir.mkdir(parents=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport=VIEWPORT,
            record_video_dir=str(video_dir),
            record_video_size=VIEWPORT,
        )
        page = await ctx.new_page()

        # Cards first, on a throwaway page, so the recorded one stays clean.
        cards = await ctx.new_page()
        await _card(cards, WORK / "card_open.png", "LLM Wiki",
                    "Your documents, compiled once into a wiki you can read — "
                    "and question, with a citation for every fact.")
        await _card(cards, WORK / "card_end.png", "Local-first. Cited. Yours.",
                    "github.com/Clod/llmwiki-marimo")
        await cards.close()

        await page.goto(url, wait_until="networkidle", timeout=60_000)
        await page.wait_for_timeout(2_500)
        await page.evaluate(_OVERLAY)
        tl = Timeline()

        # ── 1. Read tab ────────────────────────────────────────────────────
        await _say(page, "Every page here was written by a model, from your PDFs.", 3.2)
        row = page.locator("table tbody tr").filter(has_text="Cinderella").first
        await _glide(page, row.locator("td").first)
        await page.wait_for_timeout(2_200)
        await _say(page, "Definition, context, sources — and links to related pages "
                         "it wrote itself.", 4.0)
        await page.mouse.wheel(0, 700)
        await page.wait_for_timeout(2_000)
        await page.mouse.wheel(0, -700)
        await _say(page, "", 0.3)

        # ── 2. Chat tab ────────────────────────────────────────────────────
        await page.evaluate(_OVERLAY)   # re-inject: the tab switch re-renders
        await _glide(page, page.get_by_text("💬 Chat", exact=False).last)
        await page.locator("marimo-chatbot").last.wait_for(state="attached", timeout=15_000)
        await page.wait_for_timeout(1_200)
        await page.evaluate(_OVERLAY)
        await _say(page, "Two checkboxes decide who does the looking — "
                         "and that is the whole design.", 4.2)

        # ── 3. Strict mode: a real, cited answer ───────────────────────────
        await _say(page, "Strict mode: the model searches, then code checks it "
                         "cited something real.", 3.6)
        await _ask(page, COVERED_QUESTION)
        took = await _await_answer(page, tl)
        await _say(page, f"Two documents, one answer — every claim naming its page. "
                         f"({took:.0f}s)", 4.5)

        # ── 4. Save to wiki ────────────────────────────────────────────────
        save = page.get_by_text("Save the last response to the wiki", exact=False).last
        if await save.count():
            await _glide(page, save)
            await page.wait_for_timeout(1_400)
            await _say(page, "Good answers become permanent pages — on your click. "
                             "The agent has no write tool.", 4.2)
            await _glide(page, save)      # collapse again
            await page.wait_for_timeout(600)

        # ── 5. Pre-retrieval: refuse without paying ────────────────────────
        await _say(page, "Now the other mode: code retrieves first, and can refuse "
                         "before the model is ever called.", 4.4)
        pre = page.get_by_role("checkbox", name="code retrieves from the wiki")
        if await pre.count():
            await _glide(page, pre.first)
            await page.wait_for_timeout(1_200)
        await _say(page, "", 0.2)
        await _ask(page, OFF_CORPUS_QUESTION)
        took = await _await_answer(page, tl)
        await _say(page, f"Refused in {took:.1f}s — not because the model is fast, "
                         f"but because it was never asked.", 5.0)
        await _say(page, "", 0.4)

        path = Path(await page.video.path())
        await ctx.close()
        await browser.close()
        return path, tl


# ── post-production ───────────────────────────────────────────────────────────

def _ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def _build(raw: Path, tl: Timeline, out: Path) -> None:
    """Speed up the marked waits, bookend with title cards, write an mp4."""
    parts: list[Path] = []

    card_open, card_end = WORK / "card_open.png", WORK / "card_end.png"
    seg = WORK / "seg_open.mp4"
    _ffmpeg(["-loop", "1", "-i", str(card_open), "-t", "3", "-r", str(FPS),
             "-vf", "format=yuv420p", str(seg)])
    parts.append(seg)

    # Alternate normal / sped-up spans across the recording.
    cuts: list[tuple[float, float, bool]] = []
    cursor = 0.0
    for a, b in tl.spans:
        if a > cursor:
            cuts.append((cursor, a, False))
        cuts.append((a, b, True))
        cursor = b
    cuts.append((cursor, -1.0, False))

    for i, (start, end, fast) in enumerate(cuts):
        if end >= 0 and end - start < 0.35:
            continue
        seg = WORK / f"seg_{i:02d}.mp4"
        args = ["-i", str(raw), "-ss", f"{start:.3f}"]
        if end >= 0:
            args += ["-to", f"{end:.3f}"]
        vf = f"setpts={1/SPEEDUP:.4f}*PTS,format=yuv420p" if fast else "format=yuv420p"
        args += ["-an", "-r", str(FPS), "-vf", vf, str(seg)]
        _ffmpeg(args)
        parts.append(seg)

    seg = WORK / "seg_end.mp4"
    _ffmpeg(["-loop", "1", "-i", str(card_end), "-t", "3", "-r", str(FPS),
             "-vf", "format=yuv420p", str(seg)])
    parts.append(seg)

    listing = WORK / "concat.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in parts))
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing),
             "-c:v", "libx264", "-preset", "slow", "-crf", "23",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)])


def main() -> None:
    require_llm_config(
        settings.LLM_BASE_URL, settings.LLM_API_KEY, settings.LLM_MODEL,
        purpose="the demo video (the answers in it are real)",
    )
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg not on PATH")
    if not (WIKI / "wiki").exists():
        raise SystemExit(f"no wiki at {WIKI}")

    port = _free_port()
    marimo_bin = Path(sys.executable).parent / "marimo"
    print(f"launching read_app_tabs.py on :{port}…")
    proc = subprocess.Popen(
        [str(marimo_bin), "run", "marimo/read_app_tabs.py",
         "--port", str(port), "--headless", "--no-token", "--no-sandbox"],
        cwd=str(_PROJECT_ROOT),
        env={**os.environ, "WIKI_PATH": str(WIKI)},
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
    )
    try:
        if not _wait_for_port(port):
            raise SystemExit("the app never came up")
        time.sleep(2)
        print("recording…")
        raw, tl = asyncio.run(_drive(f"http://localhost:{port}"))
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    (WORK / "timeline.json").write_text(json.dumps({"spans": tl.spans}, indent=2))
    out = ASSETS / "demo.mp4"
    print(f"post-producing ({len(tl.spans)} span(s) sped up {SPEEDUP:g}×)…")
    _build(raw, tl, out)
    size = out.stat().st_size / 1_000_000
    dur = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(out)],
        capture_output=True, text=True).stdout.strip()
    print(f"wrote {out.relative_to(_PROJECT_ROOT)} — {float(dur):.1f}s, {size:.1f} MB")
    print(f"raw capture kept at {raw}")


if __name__ == "__main__":
    main()
