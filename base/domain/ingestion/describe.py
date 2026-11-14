"""Describe the kept images of a source with a vision model (docs/design_image_handling.md).

`describe_images` runs after the text of a source was extracted and its images were
kept in `.llmwiki/cache/local/<document id>/images/` (`domain/ingestion/images.py`).
For each image it decides one of four outcomes (D2, D4):

- described: a `<file>.desc.json` file holds the answer of the vision model. The file
  is reused while its `image_sha256` and `prompt_sha256` match (also when it comes from
  another document of the wiki); otherwise the model is called, once, with one retry
  for an answer that is not the expected JSON.
- decorative: the model (or the cached file) says the image carries no information.
- skipped, with the reason `too_small`, `duplicate`, `over_limit` or `failed`.
- pending: no file, no reason, and no model was called (the rebuild of the index calls
  none, a missing configuration offers none).

The link line of a described image in the stored text is followed by a delimited block
(D5); the link line of an image that was skipped or is pending is followed by a marker.
`strip_descriptions` undoes both, so the same function can describe a text again.

Nodes of the writing diagram: I17 (images kept), I18 (one image described), I19 (one
image skipped).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from domain import tracing
from domain.i18n import SUPPORTED_LANGUAGES, Locale, get_locale
from domain.prompts import load_prompt
from domain.wiki_settings import ImagesConfig

from .formats import IMAGE_EXTENSIONS
from .images import (
    DECORATIVE, DESC_SUFFIX, DUPLICATE, FAILED, IMAGE_LINK_RE, IMAGES_DIRNAME, OVER_LIMIT,
    TOO_SMALL, alt_page, desc_name, document_dir, enrich_figures, file_sha256, image_size,
    read_description, read_figures, write_figures,
)

logger = logging.getLogger(__name__)

PROMPT_NAME = "image_description"
NOTE_PROMPT_NAME = "image_note"
BLOCK_OPEN = "<!-- image-description: {file} -->"
BLOCK_CLOSE = "<!-- /image-description -->"
# One whole block: from its opening delimiter to its closing delimiter.
BLOCK_RE = re.compile(
    r"<!-- image-description: (?P<file>[^\n]*?) -->\n.*?\n<!-- /image-description -->", re.DOTALL)
_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".webp": "image/webp", ".gif": "image/gif"}
_KEYS = ("type", "decorative", "description", "visible_text", "values")


@dataclass(frozen=True)
class Vision:
    """The vision model: an OpenAI-compatible client and the model name."""

    client: Any
    model: str


def default_vision() -> Vision | None:
    """The vision model of the environment (`VISION_*`, then `WIKI_LLM_*`, then `LLM_*`),
    or None when a value is missing. No request is made."""
    from config import settings

    base_url, api_key, model = settings.vision()
    if not (base_url.strip() and api_key.strip() and model.strip()):
        return None
    from openai import OpenAI

    return Vision(OpenAI(base_url=base_url, api_key=api_key), model)


def effective_config(config: ImagesConfig, filename: str) -> ImagesConfig:
    """The `[images]` settings that apply to the source `filename`. An image file is
    described whatever `describe` says, and none of its size filters applies: its text
    is the description (D11 of the image design). With `ignore` nothing is described."""
    if config.ignore:
        return replace(config, describe=False)
    if Path(filename).suffix.lower() in IMAGE_EXTENSIONS:
        return replace(config, describe=True, min_side_px=0, min_bytes=0,
                       max_per_document=max(1, config.max_per_document))
    return config


def prompt_text(language: str) -> str:
    return load_prompt(PROMPT_NAME, language)


def prompt_sha256(language: str) -> str:
    return hashlib.sha256(prompt_text(language).encode("utf-8")).hexdigest()


def note_for(language: str) -> str:
    """The sentence the ingestion prompts gain when a text holds descriptions (D6)."""
    return load_prompt(NOTE_PROMPT_NAME, language).strip()


# ── The block and the marker ──────────────────────────────────────────────────

def _one_line(text: Any) -> str:
    """`text` on one line, with nothing that could close or open a delimiter."""
    return " ".join(str(text).replace("<!--", "").replace("-->", "").split())


def description_block(file: str, page: int | str, answer: dict, model: str, loc: Locale) -> str:
    """The delimited block of D5 for the image `file` found on `page`."""
    head = f"> **{loc.lbl_image_generated.format(page=page)}** ({loc.lbl_image_model} {_one_line(model)})."
    fields = []
    if answer.get("type"):
        fields.append(f"**{loc.lbl_type}:** {_one_line(answer['type'])}.")
    if answer.get("description"):
        fields.append(f"**{loc.lbl_image_description}:** {_one_line(answer['description'])}")
    lines = [BLOCK_OPEN.format(file=_one_line(file)), head]
    if fields:
        lines.append("> " + " ".join(fields))
    if answer.get("visible_text"):
        lines.append(f"> **{loc.lbl_image_visible_text}:** " + "; ".join(_one_line(t) for t in answer["visible_text"]))
    if answer.get("values"):
        lines.append(f"> **{loc.lbl_image_values}:** " + "; ".join(_one_line(v) for v in answer["values"]))
    lines.append(BLOCK_CLOSE)
    return "\n".join(lines)


def not_described_marker(page: int | str, loc: Locale) -> str:
    return loc.image_not_described.format(page=page)


def _marker_regex() -> re.Pattern[str]:
    """A marker of any language, with the page number free."""
    forms = {get_locale(code).image_not_described for code in SUPPORTED_LANGUAGES}
    alternatives = "|".join(re.escape(f).replace(re.escape("{page}"), r"\d+") for f in sorted(forms))
    return re.compile(rf"\n\n(?:{alternatives})")


_MARKER_RE = _marker_regex()
_BLOCK_AFTER_LINK_RE = re.compile(
    r"\n\n<!-- image-description: [^\n]*? -->\n.*?\n<!-- /image-description -->", re.DOTALL)


def strip_descriptions(text: str) -> str:
    """`text` as extraction wrote it: the blocks and the markers that follow a link
    line are removed. A text with neither is returned unchanged."""
    out = _BLOCK_AFTER_LINK_RE.sub("", text)
    return _MARKER_RE.sub("", out) if "\n\n[" in out else out


def block_files(text: str) -> list[str]:
    """The image file of each description block of `text`, in order."""
    return [m.group("file") for m in BLOCK_RE.finditer(text or "")]


# ── The vision call ───────────────────────────────────────────────────────────

class BadAnswer(ValueError):
    """The vision model answered something that is not the JSON the prompt asks for."""


def parse_answer(raw: str) -> dict:
    """The answer of the vision model as a dict with the five fixed keys; `BadAnswer`
    when it is not a JSON object of that shape (D6)."""
    text = re.sub(r"^```(?:json)?\s*", "", (raw or "").strip(), flags=re.IGNORECASE)
    text = re.sub(r"\s*```\s*$", "", text).strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise BadAnswer("the answer is not JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("decorative"), bool):
        raise BadAnswer("the answer lacks a boolean 'decorative'")
    answer: dict = {"type": str(data.get("type") or "").strip(), "decorative": data["decorative"]}
    description = data.get("description")
    if not isinstance(description, str) or (not description.strip() and not data["decorative"]):
        raise BadAnswer("the answer lacks a 'description'")
    answer["description"] = description.strip()
    for key in ("visible_text", "values"):
        items = data.get(key, [])
        if not isinstance(items, list):
            raise BadAnswer(f"'{key}' is not a list")
        answer[key] = [str(i).strip() for i in items if str(i).strip()]
    return answer


def _request(image: Path, language: str) -> list[dict]:
    mime = _MIME.get(image.suffix.lower(), "application/octet-stream")
    data = base64.b64encode(image.read_bytes()).decode("ascii")
    return [
        {"role": "system", "content": prompt_text(language)},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}},
        ]},
    ]


def call_vision(vision: Vision, image: Path, language: str) -> tuple[dict, int, int]:
    """Ask the vision model about `image`; return (answer, input tokens, output tokens).

    A malformed answer is asked for once more; a second one raises `BadAnswer`. An
    exception of the client (timeout, refusal) propagates.
    """
    client = tracing.wrap_openai(vision.client)
    messages = _request(image, language)
    tokens_in = tokens_out = 0
    last: BadAnswer | None = None
    for _attempt in range(2):
        with tracing.image_file(image.name):
            response = client.chat.completions.create(model=vision.model, messages=messages, temperature=0)
        usage = getattr(response, "usage", None)
        tokens_in += int(getattr(usage, "prompt_tokens", 0) or 0)
        tokens_out += int(getattr(usage, "completion_tokens", 0) or 0)
        try:
            return parse_answer(response.choices[0].message.content or ""), tokens_in, tokens_out
        except BadAnswer as exc:
            last = exc
    raise last  # type: ignore[misc]


# ── The decision for each image ───────────────────────────────────────────────

@dataclass
class Outcome:
    """What happened to one image. `kind` is described, decorative, skipped or pending."""

    file: str
    page: int
    kind: str
    reason: str = ""
    answer: dict = field(default_factory=dict)
    model: str = ""
    cached: bool = False
    needs_call: bool = False


@dataclass
class DescribeResult:
    pages: list[tuple[int, str]]
    images: int = 0
    described: int = 0
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def calls_needed(self) -> int:
        return sum(1 for o in self.outcomes if o.needs_call)


def _valid(desc: dict | None, sha: str, prompt_sha: str) -> bool:
    return bool(desc) and desc.get("image_sha256") == sha and desc.get("prompt_sha256") == prompt_sha


def _other_descriptions(workspace: Path, doc_id: str, prompt_sha: str) -> dict[str, Path]:
    """image sha256 → a valid description file of another document of the wiki."""
    found: dict[str, Path] = {}
    root = document_dir(workspace, doc_id).parent
    if not root.is_dir():
        return found
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and p.name != doc_id):
        try:
            figures = json.loads((folder / "figures.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for fig in figures if isinstance(figures, list) else []:
            if not isinstance(fig, dict):
                continue
            name, sha = fig.get("description"), fig.get("sha256")
            if not (isinstance(name, str) and (name.endswith(DESC_SUFFIX) or name == DECORATIVE)
                    and sha and isinstance(fig.get("file"), str)) or sha in found:
                continue
            path = folder / IMAGES_DIRNAME / desc_name(fig["file"])
            if _valid(read_description(path), sha, prompt_sha):
                found[sha] = path
    return found


def _write_description(path: Path, sha: str, vision: Vision, prompt_sha: str, language: str,
                       answer: dict, tokens_in: int, tokens_out: int) -> None:
    path.write_text(json.dumps({
        "image_sha256": sha, "model": vision.model, "prompt_sha256": prompt_sha, "language": language,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tokens_input": tokens_in, "tokens_output": tokens_out, "answer": answer,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def describe_images(
    page_contents: list[tuple[int, str]],
    *,
    workspace: Path,
    doc_id: str,
    language: str,
    config: ImagesConfig,
    vision: Vision | None = None,
    allow_calls: bool = True,
    dry_run: bool = False,
) -> DescribeResult:
    """Describe the kept images of the document `doc_id` and put the descriptions in
    the text of its pages; return the new pages and the counts.

    `page_contents` is the text as extraction wrote it. With `config.describe` false the
    pages come back unchanged. `figures.json` always gets `sha256`, `width`, `height`
    and `description` (the file name of the description, or the reason, or empty).
    With `vision` None, or `allow_calls` false, no model is called: an image with a
    valid description is described, the others stay pending. With `dry_run` nothing is
    written and no model is called; `calls_needed` of the result counts the calls a
    real run would make.
    """
    doc_dir = document_dir(workspace, doc_id)
    figures = enrich_figures(doc_dir) if not dry_run else _figures_for_plan(doc_dir)
    if not figures:
        return DescribeResult(list(page_contents))
    folder = doc_dir / IMAGES_DIRNAME
    W = tracing.WRITING
    if not dry_run:
        tracing.mark("I17", W, count=len(figures),
                     total_bytes=sum((folder / f["file"]).stat().st_size
                                     for f in figures if (folder / f["file"]).is_file()))
    if not config.describe:
        return DescribeResult(list(page_contents), images=len(figures))

    loc = get_locale(language)
    prompt_sha = prompt_sha256(language)
    calls_allowed = vision is not None and allow_calls and not dry_run
    others: dict[str, Path] | None = None
    outcomes: list[Outcome] = []
    seen: set[str] = set()
    counted = 0
    for fig in figures:
        file, page, sha = fig["file"], int(fig.get("page") or 0), fig.get("sha256", "")
        path = folder / file
        outcome = Outcome(file, page, "pending")
        outcomes.append(outcome)
        if not path.is_file() or not sha:
            outcome.kind, outcome.reason = "skipped", FAILED
        elif (fig.get("width") and min(fig["width"], fig["height"]) < config.min_side_px) \
                or path.stat().st_size < config.min_bytes:
            outcome.kind, outcome.reason = "skipped", TOO_SMALL
        elif sha in seen:
            outcome.kind, outcome.reason = "skipped", DUPLICATE
        elif counted >= config.max_per_document:
            outcome.kind, outcome.reason = "skipped", OVER_LIMIT
        else:
            seen.add(sha)
            counted += 1
            own = folder / desc_name(file)
            desc = read_description(own)
            if not _valid(desc, sha, prompt_sha):
                if others is None:
                    others = _other_descriptions(workspace, doc_id, prompt_sha)
                if sha in others:
                    desc = read_description(others[sha])
                    if not dry_run:
                        own.write_bytes(others[sha].read_bytes())
                else:
                    desc = None
            if desc is not None:
                _settle(outcome, desc, trace=not dry_run)
            elif calls_allowed:
                _call(outcome, vision, path, own, sha, prompt_sha, language)
            else:
                outcome.needs_call = True

    if dry_run:
        return DescribeResult(list(page_contents), images=len(figures), outcomes=outcomes)

    for fig, outcome in zip(figures, outcomes):
        fig["description"] = (desc_name(outcome.file) if outcome.kind == "described"
                              else outcome.reason if outcome.kind in ("skipped", "decorative") else "")
        if outcome.kind == "skipped":
            tracing.mark("I19", W, file=outcome.file, reason=outcome.reason)
    write_figures(doc_dir, figures)

    by_file = {o.file: o for o in outcomes}

    def rewrite(match: re.Match[str]) -> str:
        outcome = by_file.get(match.group("file"))
        link = match.group(0)
        if outcome is None or outcome.kind == "decorative" or outcome.reason == DUPLICATE:
            return link
        page = outcome.page or alt_page(match.group("alt")) or "?"
        if outcome.kind == "described":
            return f"{link}\n\n{description_block(outcome.file, page, outcome.answer, outcome.model, loc)}"
        return f"{link}\n\n{not_described_marker(page, loc)}"

    pages = [(number, IMAGE_LINK_RE.sub(rewrite, text)) for number, text in page_contents]
    return DescribeResult(pages, images=len(figures),
                          described=sum(1 for o in outcomes if o.kind == "described"), outcomes=outcomes)


def _figures_for_plan(doc_dir: Path) -> list[dict]:
    """The figures with their hashes, without writing `figures.json`: the values the
    file holds, and the hash and size of an image the file does not describe yet."""
    figures = read_figures(doc_dir)
    folder = doc_dir / IMAGES_DIRNAME
    out = []
    for fig in figures:
        fig = dict(fig)
        path = folder / fig["file"]
        if path.is_file() and not fig.get("sha256"):
            fig["sha256"] = file_sha256(path)
            fig["width"], fig["height"] = image_size(path)
        out.append(fig)
    return out


def _settle(outcome: Outcome, desc: dict, *, trace: bool) -> None:
    """Take the verdict of a valid description file."""
    outcome.answer = desc["answer"]
    outcome.model = str(desc.get("model", ""))
    outcome.cached = True
    outcome.kind = "decorative" if desc["answer"].get("decorative") else "described"
    if outcome.kind == "decorative":
        outcome.reason = DECORATIVE
    if not trace:
        return
    tracing.mark("I18", tracing.WRITING, file=outcome.file, image_sha256=desc.get("image_sha256"),
                 model=outcome.model, cached=True, tokens_input=desc.get("tokens_input", 0),
                 tokens_output=desc.get("tokens_output", 0))
    if outcome.kind == "decorative":
        tracing.mark("I19", tracing.WRITING, file=outcome.file, reason=DECORATIVE)


def _call(outcome: Outcome, vision: Vision, image: Path, desc_path: Path, sha: str,
          prompt_sha: str, language: str) -> None:
    """Describe one image with the model; a failure of any kind is the reason `failed`."""
    with tracing.node("I18", tracing.WRITING, file=outcome.file, image_sha256=sha,
                      model=vision.model, cached=False) as node:
        try:
            answer, tokens_in, tokens_out = call_vision(vision, image, language)
        except Exception as exc:  # noqa: BLE001 — a failed call must not fail the ingestion
            logger.warning("could not describe %s: %s", outcome.file, exc)
            node.set(error=str(exc)[:300])
            outcome.kind, outcome.reason = "skipped", FAILED
            tracing.mark("I19", tracing.WRITING, file=outcome.file, reason=FAILED)
            return
        node.set(tokens_input=tokens_in, tokens_output=tokens_out)
    _write_description(desc_path, sha, vision, prompt_sha, language, answer, tokens_in, tokens_out)
    outcome.answer, outcome.model = answer, vision.model
    outcome.kind = "decorative" if answer["decorative"] else "described"
    if outcome.kind == "decorative":
        outcome.reason = DECORATIVE
        tracing.mark("I19", tracing.WRITING, file=outcome.file, reason=DECORATIVE)
