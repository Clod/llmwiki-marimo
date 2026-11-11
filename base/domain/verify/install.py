"""Install the local NLI verifier into a running environment: the commands, and the status.

The verifier needs the packages of `requirements-verify.txt` (the `verify` group of
`pyproject.toml`) and the NLI model in the Hugging Face cache (`nli.download_model`).
The web interface runs these commands in the environment of its own server, so they only
add packages: `uv sync --group verify` would remove the packages of the other groups,
the web interface among them.

The logic follows `quickstart.py` (`verifier_install_commands`), which stays separate
because it imports nothing from the project.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal

# The project root: base/domain/verify/install.py → the folder of requirements-verify.txt.
ROOT = Path(__file__).resolve().parents[3]
REQUIREMENTS = ROOT / "requirements-verify.txt"

# `torch` from PyPI on Linux pulls about 2 GB of CUDA libraries; this index has the CPU
# build, the one the verifier needs. `requirements-verify.txt` leaves the CUDA packages out.
CPU_TORCH_INDEX = "https://download.pytorch.org/whl/cpu"

Status = Literal["installed", "packages", "missing"]


def pinned_version(requirements: Path, package: str) -> str | None:
    """The version `requirements` pins `package` to, or None."""
    prefix = f"{package}=="
    for line in requirements.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].split()[0].rstrip("\\").strip()
    return None


def install_commands(python: str, requirements: Path, *, uv: str | None = None,
                     platform: str = sys.platform) -> list[list[str]]:
    """The commands that install the packages of `requirements` into the environment of
    `python`, in order. With `uv` (the path of the uv executable), `uv pip install
    --python`; without it, `python -m pip install`. On Linux, `torch` comes first, from
    the CPU index; the pinned file then finds it installed."""
    pip = [uv, "pip", "install", "--python", python] if uv else [python, "-m", "pip", "install"]
    commands: list[list[str]] = []
    torch_version = pinned_version(requirements, "torch")
    if platform.startswith("linux") and torch_version:
        commands.append([*pip, f"torch=={torch_version}", "--index-url", CPU_TORCH_INDEX])
    commands.append([*pip, "--require-hashes", "-r", str(requirements)])
    return commands


def model_command(python: str, base_dir: Path) -> list[str]:
    """The command that downloads the NLI model. It runs in a process of its own: the
    server imports `huggingface_hub` only after the packages are installed."""
    code = (f"import sys; sys.path.insert(0, {str(base_dir)!r}); "
            "from domain.verify.nli import download_model; print(download_model())")
    return [python, "-c", code]


def install_steps(python: str, *, uv: str | None = None,
                  platform: str = sys.platform) -> list[tuple[str, list[str]]]:
    """Every step of the installation, in order, as (step, command): `torch` (Linux only:
    the CPU build), `packages` (the pinned file), `model` (the download of the model)."""
    packages = install_commands(python, REQUIREMENTS, uv=uv, platform=platform)
    steps = [("torch", packages[0])] if len(packages) == 2 else []
    return [*steps, ("packages", packages[-1]), ("model", model_command(python, ROOT / "base"))]


def run_command(command: list[str], progress: Callable[[str], None]) -> int:
    """Run `command` in the project root, send each non-empty line of its output (stdout
    and stderr together) to `progress`, and return its exit code. The progress bars of
    Hugging Face are off: they redraw one line with carriage returns."""
    env = {**os.environ, "PYTHONUTF8": "1", "NO_COLOR": "1", "HF_HUB_DISABLE_PROGRESS_BARS": "1"}
    with subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding="utf-8", errors="replace") as process:
        for line in process.stdout:
            if line.strip():
                progress(line.rstrip())
        return process.wait()


def status() -> Status:
    """`installed`: the packages and the model are here. `packages`: the model is missing.
    `missing`: the packages are missing. The import caches are cleared first, so a package
    installed while the server runs is found."""
    from domain.verify import nli

    importlib.invalidate_caches()
    if not nli.is_installed():
        return "missing"
    return "installed" if nli.model_is_cached() else "packages"
