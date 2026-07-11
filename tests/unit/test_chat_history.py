"""Tests for domain/chat/history.py — lean history sent to the model.

Long accumulated context degrades gpt-4o (it stops calling tools / leaks) — the
session arc in the chat trace shows it collapsing after ~3 turns. trim_history
builds a LEAN history for the model (the user still sees the full answers):
  - compact big advisory tables out of old turns (the model can re-call the
    tool; the 40-row table only bloats context),
  - cap to the last N turns.

Operates on chat messages duck-typed as `.role` ("user"/"assistant") + `.content`.
"""

from domain.chat.history import trim_history


class _Msg:
    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content


def _msgs(*pairs: tuple[str, str]) -> list[_Msg]:
    return [_Msg(role, content) for role, content in pairs]


BIG_TABLE_ANSWER = (
    "Para $1.000.000 a 90 días, la mejor opción es plazo fijo.\n\n"
    "**Alternativas con ganancia estimada** (ordenadas por ganancia)\n"
    "| opción | clave | plazo | TEA | ganancia est. | al fecha | fuente |\n"
    "|---|---|---|---|---|---|---|\n"
    "| plazo_fijo | Banco Credicoop | 90d | 41.84% | $90,000 | 2026-06-25 | bcra.gob.ar |\n"
    "> ⚠️ Ganancias nominales...\n"
    "**Renta variable** (no estimable)\n"
    "| opción | depende de |\n| acciones | precio_mercado |"
)


# ── compaction ───────────────────────────────────────────────────────────────

def test_compacts_advisory_table_keeps_prose():
    out = trim_history(_msgs(("user", "¿qué me conviene?"), ("assistant", BIG_TABLE_ANSWER)))
    kept = out[-1]
    assert kept.role == "assistant"
    assert kept.content.startswith("Para $1.000.000 a 90 días")  # prose head kept
    assert "| opción" not in kept.content                         # table gone
    assert "tabla de alternativas omitida" in kept.content        # placeholder


def test_leaves_prose_answer_untouched():
    answer = "Una caución es... Fuente: cauciones-bursatiles.md"
    out = trim_history(_msgs(("user", "¿qué es caución?"), ("assistant", answer)))
    assert out[-1].content == answer


def test_leaves_user_messages_untouched():
    out = trim_history(_msgs(("user", "hola")))
    assert out[0].content == "hola"


# ── cap ──────────────────────────────────────────────────────────────────────

def test_caps_to_last_three_turns():
    pairs = []
    for i in range(5):  # 5 turns = 10 messages
        pairs += [("user", f"q{i}"), ("assistant", f"a{i}")]
    out = trim_history(_msgs(*pairs), max_turns=3)
    assert len(out) == 6                    # last 3 turns
    assert out[0].content == "q2"           # turns 2,3,4 kept
    assert out[-1].content == "a4"


def test_compaction_and_cap_together():
    pairs = [("user", "q0"), ("assistant", BIG_TABLE_ANSWER)]
    for i in range(1, 4):
        pairs += [("user", f"q{i}"), ("assistant", f"a{i}")]
    out = trim_history(_msgs(*pairs), max_turns=3)  # 4 turns -> keep last 3
    assert len(out) == 6
    assert out[0].content == "q1"                    # the big-table turn 0 dropped by cap
    assert all("| opción" not in m.content for m in out)


# ── edges ────────────────────────────────────────────────────────────────────

def test_empty_history():
    assert trim_history([]) == []


def test_preserves_order_and_roles():
    hist = _msgs(("user", "q0"), ("assistant", "a0"), ("user", "q1"))
    out = trim_history(hist, max_turns=3)
    assert [(m.role, m.content) for m in out] == [("user", "q0"), ("assistant", "a0"), ("user", "q1")]
