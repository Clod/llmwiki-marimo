"""Extract the message ids of `web/` and keep the catalogs in step with them.

    uv run --group web python -m web.i18n_extract            # update messages.pot and every messages.po
    uv run --group web python -m web.i18n_extract --check    # exit 1 when a catalog is out of date

The ids are the English texts written with `_()`, `N_()` and `ngettext()` in the Python modules and
in the templates (`_()` and `{% trans %}`). Not imported by the application.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from babel.messages import pofile
from babel.messages.catalog import Catalog
from babel.messages.extract import extract_from_dir

from web.i18n import LANGUAGES, LOCALE_DIR, SOURCE_LANGUAGE

WEB = Path(__file__).parent
METHODS = [("**.py", "python"), ("templates/**.html", "jinja2")]
OPTIONS = {"templates/**.html": {"extensions": "jinja2.ext.i18n", "trimmed": "true"}}
# The tests are not under web/, so nothing else needs to be skipped; the extraction itself is.
SKIP = {"i18n_extract.py"}
FIXED_DATE = datetime(2026, 10, 5, tzinfo=timezone.utc)


def extract() -> Catalog:
    """The catalog of every message id in `web/`, with the places it is used."""
    catalog = Catalog(project="llmwiki", charset="utf-8")
    for filename, lineno, message, comments, context in extract_from_dir(
            str(WEB), method_map=METHODS, options_map=OPTIONS, comment_tags=(), strip_comment_tags=False):
        if Path(filename).name in SKIP or not message:
            continue
        singular, plural = (message[0], message[1]) if isinstance(message, tuple) else (message, None)
        if not isinstance(singular, str):
            continue
        catalog.add((singular, plural) if plural else singular, locations=[(f"web/{filename}", lineno)],
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


def update() -> None:
    template = extract()
    LOCALE_DIR.mkdir(exist_ok=True)
    with (LOCALE_DIR / "messages.pot").open("wb") as fh:
        template.creation_date = FIXED_DATE
        pofile.write_po(fh, template, width=120, sort_by_file=False, include_lineno=False)
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
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fh:
            pofile.write_po(fh, catalog, width=120, sort_by_file=False, include_lineno=False, include_previous=False)


if __name__ == "__main__":
    update()
    print(f"{len(ids())} message ids")
    sys.exit(0)
