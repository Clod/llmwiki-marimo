"""The messages of base/ that the interface translates (`domain/messages.py`)."""

from domain.messages import M_, Message


def test_a_message_is_its_english_text():
    message = M_("🔗 Cross-linked %(n)s page(s)", n=3)
    assert message == "🔗 Cross-linked 3 page(s)" and isinstance(message, str)
    assert f"{message}" == "🔗 Cross-linked 3 page(s)"


def test_a_message_keeps_its_id_and_values():
    message = M_("🔍 Validating %(file)s", file="a.pdf")
    assert isinstance(message, Message)
    assert message.msgid == "🔍 Validating %(file)s" and message.params == {"file": "a.pdf"}


def test_a_message_without_values_is_its_id():
    assert M_("✅ No issues found.") == "✅ No issues found."
