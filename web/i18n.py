"""The interface language: catalogs, negotiation and the translation functions.

The message ids are the English texts. The catalogs are GNU gettext files,
`web/locale/<lang>/LC_MESSAGES/messages.po`, compiled in memory when the module
loads (no `.mo` file is written or committed). English is the source language and
has no catalog. A new language needs one catalog and one entry in `LANGUAGES`.

This is the *interface* language: the labels, notices and console lines that `web/`
writes. The language of a wiki (`[wiki].language`) is a different setting and
governs what ends up inside the wiki.

No FastAPI import: the language of the running request is a context variable that
`web.app` sets (`language_middleware`), so `_()` works in a route, in a template, in
a thread started from a route (`web.state.start_operation` copies the context) and,
in a test of a pure module, with `use_language`.
"""

from __future__ import annotations

import contextvars
import io
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from babel.messages import mofile, pofile
from babel.support import NullTranslations, Translations

# Every supported language: code → name in its own language (shown by the switch).
# Adding a language: its code here and its catalog under `locale/`.
LANGUAGES: dict[str, str] = {"en": "English", "es": "Español"}
SOURCE_LANGUAGE = "en"
DEFAULT_LANGUAGE = "en"
COOKIE = "ui_lang"
LOCALE_DIR = Path(__file__).parent / "locale"

_current: contextvars.ContextVar[str] = contextvars.ContextVar("ui_language", default=DEFAULT_LANGUAGE)


def _load(lang: str) -> NullTranslations:
    path = LOCALE_DIR / lang / "LC_MESSAGES" / "messages.po"
    if lang == SOURCE_LANGUAGE or not path.is_file():
        return NullTranslations()
    with path.open("rb") as fh:
        catalog = pofile.read_po(fh, locale=lang)
    buffer = io.BytesIO()
    mofile.write_mo(buffer, catalog)
    buffer.seek(0)
    return Translations(buffer)


_TRANSLATIONS = {lang: _load(lang) for lang in LANGUAGES}


def get_language() -> str:
    return _current.get()


def set_language(lang: str) -> contextvars.Token:
    return _current.set(lang if lang in LANGUAGES else DEFAULT_LANGUAGE)


@contextmanager
def use_language(lang: str) -> Iterator[None]:
    token = set_language(lang)
    try:
        yield
    finally:
        _current.reset(token)


def _fill(text: str, variables: dict) -> str:
    return text % variables if variables else text


def raw_gettext(message: str) -> str:
    return _TRANSLATIONS[get_language()].gettext(message)


def raw_ngettext(singular: str, plural: str, n: int) -> str:
    return _TRANSLATIONS[get_language()].ngettext(singular, plural, n)


def raw_pgettext(context: str, message: str) -> str:
    return _TRANSLATIONS[get_language()].pgettext(context, message)


def raw_npgettext(context: str, singular: str, plural: str, n: int) -> str:
    return _TRANSLATIONS[get_language()].npgettext(context, singular, plural, n)


def gettext(message: str, **variables) -> str:
    """`message` in the current language, with `%(name)s` placeholders filled."""
    return _fill(raw_gettext(message), variables)


def ngettext(singular: str, plural: str, n: int, **variables) -> str:
    """The singular or the plural form for `n`; `%(n)s` is filled with `n`."""
    return _fill(raw_ngettext(singular, plural, n), {"n": n, **variables})


# `_` translates now; `N_` only marks a text for extraction, for a constant defined
# at import time that is translated where it is used.
_ = gettext


def pgettext(context: str, message: str, **variables) -> str:
    """`message` as `context` uses it: the same English text can need two Spanish ones."""
    return _fill(raw_pgettext(context, message), variables)


def N_(message: str) -> str:
    return message


def parse_accept_language(header: str) -> str | None:
    """The supported language the browser prefers, or None: the highest `q` whose
    primary subtag (`es-AR` → `es`) is in `LANGUAGES`."""
    ranked: list[tuple[float, int, str]] = []
    for position, part in enumerate(header.split(",")):
        tag, _sep, params = part.strip().partition(";")
        tag = tag.strip().lower()
        if not tag or tag == "*":
            continue
        quality = 1.0
        for param in params.split(";"):
            name, _eq, value = param.strip().partition("=")
            if name.strip() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        if quality > 0 and tag.split("-")[0] in LANGUAGES:
            ranked.append((-quality, position, tag.split("-")[0]))
    return min(ranked)[2] if ranked else None


def negotiate(cookie: str | None, accept_language: str | None) -> str:
    """The cookie, else `Accept-Language`, else the default."""
    if cookie in LANGUAGES:
        return cookie
    return parse_accept_language(accept_language or "") or DEFAULT_LANGUAGE
