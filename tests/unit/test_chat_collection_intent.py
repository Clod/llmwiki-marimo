"""Tests for domain/chat/scope.py::collection_intent.

`collection_intent` is the second, narrower door alongside `advisory_intent`:
the roster (`in_roster`) can only say yes to a question that NAMES a covered
item, so a question about the collection as a whole ("what tales are in this
wiki?") is uncovered by construction — this function recognises that shape so
the gate can inject the overview/index instead of refusing. Cases mirror the
fairy-tales demo's own `suggested_prompts` (English and Spanish), plus ordinary
single-subject questions that must NOT trigger it.
"""

from domain.chat.scope import collection_intent

# ── True: the fairy-tales demo's own suggested prompts (both languages) ──────


def test_true_what_tales_are_in_this_wiki():
    assert collection_intent("What tales are in this wiki?") is True


def test_true_summarize_the_plot_of_each_tale():
    assert collection_intent("Summarize the plot of each tale") is True


def test_true_what_characters_and_themes_do_the_tales_share():
    assert collection_intent("What characters and themes do the tales share?") is True


def test_true_compare_how_each_story_ends():
    assert collection_intent("Compare how each story ends") is True


def test_true_que_cuentos_hay_en_este_wiki():
    assert collection_intent("¿Qué cuentos hay en este wiki?") is True


def test_true_resume_la_trama_de_cada_cuento():
    assert collection_intent("Resume la trama de cada cuento") is True


def test_true_que_personajes_y_temas_comparten_los_cuentos():
    assert collection_intent("¿Qué personajes y temas comparten los cuentos?") is True


def test_true_compara_como_termina_cada_historia():
    assert collection_intent("Compara cómo termina cada historia") is True


# ── False: ordinary single-subject questions (the anti-fire-on-everything guard)


def test_false_ordinary_concept_question_en():
    assert collection_intent("What is a glass slipper?") is False


def test_false_ordinary_data_question_es():
    assert collection_intent("¿A cuánto está el dólar MEP?") is False


def test_false_ordinary_who_question_en():
    assert collection_intent("Who is the fairy godmother?") is False
