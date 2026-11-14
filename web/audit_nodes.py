"""The short text of each node of the reading diagram, for the audit page of an answer.

The English texts are `NODE_TEXTS`, in `base/domain/chat/workflow/node_texts.py`, so
the script that writes `docs/workflow_diagrams.md` reads them without the web
dependencies. The Spanish text is in `locale/es/LC_MESSAGES/messages.po`. A node that
is not there is shown by its identifier alone (`text`).
"""

from __future__ import annotations

from domain.chat.workflow.node_texts import NODE_TEXTS
from web.i18n import _

__all__ = ["NODE_TEXTS", "text"]


def text(node_id: str) -> str:
    """The short text of a node in the interface language; '' when the node has none."""
    return _(NODE_TEXTS[node_id]) if node_id in NODE_TEXTS else ""
