"""The notices a redirect carries in its URL (`?notice=`), translated when shown.

A redirect after an action puts the notice in the URL of the screen it returns to.
The URL carries the key of the notice and its values, never the text: a link kept
and opened later, or with the interface in another language, shows the notice in
the language of the screen that displays it. An unknown key shows nothing.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Callable, Mapping

from web.deps import BUSY
from web.i18n import N_, _, ngettext

# Key → message id, or a function of `n` for a notice with a plural form: the call to
# `ngettext` is written out so that the extraction records the plural entry.
NOTICES: dict[str, str | Callable[..., str]] = {
    "busy": BUSY,
    "verifier-missing": N_("The local verifier is not installed."),
    "verify-failed": N_("Could not verify: %(error)s"),
    "page-conflict": N_("%(page)s changed since it was opened (version %(version)s)"),
    "stale-deleted": lambda n, **values: ngettext("Deleted %(n)s stale page.", "Deleted %(n)s stale pages.", n,
                                                  **values),
}


def notice_query(key: str, **values) -> str:
    """The query string of a notice: `notice=<key>` and its values."""
    if key not in NOTICES:
        raise KeyError(key)
    return urllib.parse.urlencode({"notice": key, **{k: str(v) for k, v in values.items()}})


def notice_text(key: str, values: Mapping[str, str]) -> str:
    """The notice of `key` in the interface language, filled with `values`; '' for no
    key or an unknown one."""
    entry = NOTICES.get(key or "")
    if entry is None:
        return ""
    fields = {k: v for k, v in values.items() if k != "notice"}
    if callable(entry):
        return entry(int(fields.pop("n", 0) or 0), **fields)
    return _(entry, **fields)
