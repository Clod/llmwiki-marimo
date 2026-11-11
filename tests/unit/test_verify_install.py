"""The commands that install the local verifier into a running environment."""

from __future__ import annotations

import sys

from domain.verify import install, nli


def test_without_uv_pip_installs_the_pinned_file():
    commands = install.install_commands("/env/python", install.REQUIREMENTS, platform="darwin")
    assert commands == [["/env/python", "-m", "pip", "install", "--require-hashes", "-r", str(install.REQUIREMENTS)]]


def test_with_uv_the_packages_go_to_the_python_of_the_server():
    commands = install.install_commands("/env/python", install.REQUIREMENTS, uv="/bin/uv", platform="win32")
    assert commands == [["/bin/uv", "pip", "install", "--python", "/env/python",
                         "--require-hashes", "-r", str(install.REQUIREMENTS)]]


def test_on_linux_torch_comes_first_from_the_cpu_index():
    first, second = install.install_commands("/env/python", install.REQUIREMENTS, uv="/bin/uv", platform="linux")
    version = install.pinned_version(install.REQUIREMENTS, "torch")
    assert version
    assert first == ["/bin/uv", "pip", "install", "--python", "/env/python", f"torch=={version}",
                     "--index-url", install.CPU_TORCH_INDEX]
    assert second[-2:] == ["-r", str(install.REQUIREMENTS)]


def test_no_step_removes_packages():
    """`uv sync --group verify` would remove the other groups: no step syncs."""
    for platform in ("linux", "darwin", "win32"):
        for step, command in install.install_steps("/env/python", uv="/bin/uv", platform=platform):
            assert "sync" not in command, (platform, step)


def test_the_steps_end_with_the_model():
    steps = install.install_steps("/env/python", platform="darwin")
    assert [step for step, _command in steps] == ["packages", "model"]
    assert [step for step, _command in install.install_steps("/env/python", platform="linux")] == ["torch", "packages", "model"]
    model = steps[-1][1]
    assert model[:2] == ["/env/python", "-c"] and "download_model" in model[2]


def test_the_model_download_takes_only_the_files_the_verifier_loads(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "huggingface_hub",
                        type(sys)("huggingface_hub"))
    sys.modules["huggingface_hub"].snapshot_download = lambda *a, **k: calls.append((a, k)) or "/cache"
    assert nli.download_model() == "/cache"
    assert calls[0][1]["allow_patterns"] == nli.MODEL_FILES
    assert "model.safetensors" in nli.MODEL_FILES and not any("onnx" in p or ".bin" in p for p in nli.MODEL_FILES)


def test_run_command_streams_the_lines_and_returns_the_code():
    lines = []
    code = install.run_command([sys.executable, "-c", "print('one'); print(); print('two'); raise SystemExit(3)"],
                               lines.append)
    assert lines == ["one", "two"] and code == 3


def test_status(monkeypatch):
    monkeypatch.setattr(nli, "is_installed", lambda: False)
    assert install.status() == "missing"
    monkeypatch.setattr(nli, "is_installed", lambda: True)
    monkeypatch.setattr(nli, "model_is_cached", lambda: False)
    assert install.status() == "packages"
    monkeypatch.setattr(nli, "model_is_cached", lambda: True)
    assert install.status() == "installed"
