"""Extract the message ids of `web/` and keep the catalogs in step with them.

    uv run --group web python -m web.i18n_extract            # update messages.pot and every messages.po
    uv run --group web python -m web.i18n_extract --check    # exit 1 when a catalog is out of date

The ids are the English texts written with `_()`, `N_()` and `ngettext()` in the Python modules and
in the templates (`_()` and `{% trans %}`). Two marks of `base/` are extracted too: `N_()`, the labels
of the chat workflows that the audit page shows in its diagrams, and `M_()`, the progress lines and
service messages that the interface translates (`domain/messages.py`). Not imported by the application.
"""

from __future__ import annotations

import io
import sys
from datetime import datetime, timezone
from pathlib import Path

from babel.messages import pofile
from babel.messages.catalog import Catalog
from babel.messages.extract import extract_from_dir

from web.i18n import LANGUAGES, LOCALE_DIR, SOURCE_LANGUAGE

WEB = Path(__file__).parent
ROOT = WEB.parent
BASE = ROOT / "base"
# In `base/`, only the two marks of `base/` count: `_` and `ngettext` are names of `web/`.
BASE_KEYWORDS = {"N_": None, "M_": None}
METHODS = [("**.py", "python"), ("templates/**.html", "jinja2")]
OPTIONS = {"templates/**.html": {"extensions": "jinja2.ext.i18n", "trimmed": "true"}}
# The tests are not under web/, so nothing else needs to be skipped; the extraction itself is.
SKIP = {"i18n_extract.py"}
FIXED_DATE = datetime(2026, 10, 5, tzinfo=timezone.utc)


def extract() -> Catalog:
    """The catalog of every message id in `web/` and in `base/`, with the places it is used."""
    from babel.messages.extract import DEFAULT_KEYWORDS

    catalog = Catalog(project="llmwiki", charset="utf-8")
    for directory, methods, options, keywords in (
            (WEB, METHODS, OPTIONS, DEFAULT_KEYWORDS),
            (BASE, [("**.py", "python")], {}, BASE_KEYWORDS)):
        prefix = directory.relative_to(ROOT).as_posix()
        for filename, lineno, message, comments, context in extract_from_dir(
                str(directory), method_map=methods, options_map=options, keywords=keywords,
                comment_tags=(), strip_comment_tags=False):
            if Path(filename).name in SKIP or not message:
                continue
            singular, plural = (message[0], message[1]) if isinstance(message, tuple) else (message, None)
            if not isinstance(singular, str):
                continue
            catalog.add((singular, plural) if plural else singular, locations=[(f"{prefix}/{filename}", lineno)],
                        context=context)
    return catalog


def ids() -> set[str]:
    """Every singular id (a plural entry counts once, by its singular)."""
    return {m.id[0] if isinstance(m.id, tuple) else m.id for m in extract() if m.id}


def catalog_path(lang: str) -> Path:
    return LOCALE_DIR / lang / "LC_MESSAGES" / "messages.po"


def read(lang: str) -> Catalog:
    with catalog_path(lang).open("rb") as fh:
        return pofile.read_po(fh, locale=lang)


def render() -> dict[Path, bytes]:
    """The catalogs as they should be written: messages.pot and every messages.po, each
    with its bytes. Nothing is written."""
    template = extract()
    template.creation_date = FIXED_DATE
    out = io.BytesIO()
    pofile.write_po(out, template, width=120, sort_by_file=False, include_lineno=False)
    files = {LOCALE_DIR / "messages.pot": out.getvalue()}
    for lang in LANGUAGES:
        if lang == SOURCE_LANGUAGE:
            continue
        path = catalog_path(lang)
        catalog = read(lang) if path.is_file() else Catalog(locale=lang, project="llmwiki", charset="utf-8")
        catalog.update(template, no_fuzzy_matching=True)
        catalog.fuzzy = False
        catalog.msgid_bugs_address = catalog.last_translator = catalog.language_team = ""
        catalog.project, catalog.version = "llmwiki", ""
        catalog.header_comment = f"# Catalog of the llmwiki web interface, language: {lang}.\n"
        catalog.copyright_holder = "the llmwiki authors"
        # Fixed dates: a regenerated catalog differs only where a message changed.
        catalog.creation_date = catalog.revision_date = FIXED_DATE
        out = io.BytesIO()
        pofile.write_po(out, catalog, width=120, sort_by_file=False, include_lineno=False, include_previous=False)
        files[path] = out.getvalue()
    return files


def update() -> None:
    """Write messages.pot and every messages.po."""
    for path, content in render().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def stale() -> list[Path]:
    """The catalogs whose file differs from what `update` would write: `--check` lists
    them and fails, and writes nothing."""
    return [path for path, content in render().items()
            if not path.is_file() or path.read_bytes() != content]


if __name__ == "__main__":
    if "--check" in sys.argv[1:]:
        out_of_date = stale()
        for path in out_of_date:
            print(f"out of date: {path.relative_to(ROOT)}")
        print(f"{len(ids())} message ids")
        sys.exit(1 if out_of_date else 0)
    update()
    print(f"{len(ids())} message ids")
    sys.exit(0)
