"""The interface-language switch: set the cookie and return to the page."""

from __future__ import annotations

from fastapi import APIRouter, Form
from fastapi.responses import RedirectResponse

from web import i18n

router = APIRouter()

ONE_YEAR = 60 * 60 * 24 * 365


def _local(target: str) -> str:
    """`target` when it is a path of this application, else the picker: the switch must not
    become an open redirect."""
    return target if target.startswith("/") and not target.startswith("//") and "\\" not in target else "/"


@router.post("/lang")
def switch_language(lang: str = Form(...), next: str = Form("/")):
    """Remember the language in the `ui_lang` cookie and reload the page the switch was on."""
    response = RedirectResponse(_local(next), status_code=303)
    if lang in i18n.LANGUAGES:
        response.set_cookie(i18n.COOKIE, lang, max_age=ONE_YEAR, samesite="lax", path="/")
    return response
