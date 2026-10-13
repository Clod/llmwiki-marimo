from domain.chat.config import load_config
from domain.verify import DEFAULT_THRESHOLD


def write(path, body):
    (path / "wiki_config.toml").write_text(body, encoding="utf-8")
    return load_config(path)


def test_the_threshold_defaults_when_the_file_or_the_section_is_missing(tmp_path):
    assert load_config(tmp_path).verify_threshold == DEFAULT_THRESHOLD
    assert write(tmp_path, "[wiki]\nlanguage = \"es\"\n").verify_threshold == DEFAULT_THRESHOLD


def test_the_threshold_is_read_from_the_verify_section(tmp_path):
    assert write(tmp_path, "[verify]\nthreshold = 0.7\n").verify_threshold == 0.7


def test_an_invalid_threshold_falls_back_to_the_default(tmp_path):
    assert write(tmp_path, "[verify]\nthreshold = 3\n").verify_threshold == DEFAULT_THRESHOLD
    assert write(tmp_path, "[verify]\nthreshold = \"alto\"\n").verify_threshold == DEFAULT_THRESHOLD


def test_candidates_default_and_are_read_from_the_verify_section(tmp_path):
    assert load_config(tmp_path).verify_candidates == 5
    assert write(tmp_path, "[verify]\ncandidates = 3\n").verify_candidates == 3


def test_an_invalid_candidates_value_falls_back_to_the_default(tmp_path):
    for bad in ("0", "-2", "\"muchos\"", "true"):
        assert write(tmp_path, f"[verify]\ncandidates = {bad}\n").verify_candidates == 5
