"""Messages of `base/` that the interface shows in its own language.

`base/` writes the progress lines of a long operation (ingest, scan, lint and
repair, reindex, revert) and the messages of some services in English. The web
interface shows them in the interface language: each one is written with `M_()`,
which returns a `Message`.

A `Message` is a `str`: printed, logged, compared or concatenated it is the English
text, so the scripts and the tests that read it do not change. It also keeps its
message id (the English text with `%(name)s` placeholders) and its values, so the
web translates it (`web.i18n.translate_message`). `web/i18n_extract.py` extracts
the ids of `M_()` into the catalogs of the interface.

A text that the code builds by joining pieces loses the `Message` and stays in
English, as do the values themselves (a file name, an exception, a lint summary).
No import of `web/`.
"""

from __future__ import annotations


class Message(str):
    """An English text that remembers its message id and its values."""

    msgid: str
    params: dict

    def __new__(cls, msgid: str, params: dict | None = None) -> Message:
        params = dict(params or {})
        text = msgid % params if params else msgid
        message = super().__new__(cls, text)
        message.msgid = msgid
        message.params = params
        return message


def M_(msgid: str, **params) -> Message:
    """Mark a message of `base/` for the interface language; `params` fill its
    `%(name)s` placeholders."""
    return Message(msgid, params)
