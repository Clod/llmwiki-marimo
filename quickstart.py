#!/usr/bin/env python3
"""LLM Wiki — console quick-start installer.

Stdlib-only bootstrap. The *only* prerequisite on your machine is **Python
3.12+** — this script creates an isolated virtual environment, installs the
pinned dependencies into it, drops in a pre-ingested demo wiki, writes a
``.env``, and (optionally) launches the read app.

Run it from the repository root::

    python3 quickstart.py

It is interactive by default; every prompt has a sensible default. Flags let
you run it unattended (and let a future GUI front-end shell into the same
steps)::

    python3 quickstart.py --demo fairy-tales --provider ollama --yes --no-launch

Design notes
------------
- No third-party imports: this runs *before* the venv exists.
- Each concern is a small function so a tkinter wrapper can reuse them.
- Nothing is clobbered without consent (an existing ``.env`` or demo folder is
  left alone unless you confirm, or pass ``--yes``).
"""

from __future__ import annotations

import argparse
import getpass
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import NoReturn

MIN_PYTHON = (3, 12)
REPO_ROOT = Path(__file__).resolve().parent
EXAMPLES_DIR = REPO_ROOT / "examples"
DEFAULT_WIKI_HOME = REPO_ROOT / "wikis"
DEFAULT_PORT = 2720

# Provider presets — (base_url, api_key, model). Ollama mirrors .env.example.
OLLAMA_PRESET = ("http://localhost:11434/v1", "ollama", "llama3.2")
CLOUD_PRESET = ("https://openrouter.ai/api/v1", "", "anthropic/claude-haiku-4-5")


# ── Small console helpers ─────────────────────────────────────────────────────

def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(n: int, total: int, msg: str) -> None:
    say(f"\n\033[1m[{n}/{total}] {msg}\033[0m")


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        reply = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        reply = ""
    return reply or default


def confirm(prompt: str, *, assume_yes: bool, default: bool = True) -> bool:
    if assume_yes:
        return True
    d = "Y/n" if default else "y/N"
    reply = ask(f"{prompt} ({d})").lower()
    if not reply:
        return default
    return reply in ("y", "yes")


def die(msg: str) -> NoReturn:
    say(f"\n\033[31m✗ {msg}\033[0m")
    raise SystemExit(1)


# ── Preflight ─────────────────────────────────────────────────────────────────

def check_python() -> None:
    if sys.version_info < MIN_PYTHON:
        have = ".".join(map(str, sys.version_info[:3]))
        want = ".".join(map(str, MIN_PYTHON))
        die(
            f"Python {want}+ required, but this interpreter is {have}.\n"
            f"  Install a newer Python (https://www.python.org/downloads/) and "
            f"re-run with it, e.g.  python3.12 quickstart.py"
        )


def check_repo_layout() -> None:
    needed = [
        REPO_ROOT / "requirements.txt",
        REPO_ROOT / "marimo" / "read_app.py",
        EXAMPLES_DIR,
    ]
    missing = [str(p.relative_to(REPO_ROOT)) for p in needed if not p.exists()]
    if missing:
        die(
            "This does not look like the llmwiki repo root (missing: "
            + ", ".join(missing)
            + ").\n  Run quickstart.py from the cloned repository directory."
        )


# ── Demo selection & install ──────────────────────────────────────────────────

def available_demos() -> list[str]:
    if not EXAMPLES_DIR.is_dir():
        return []
    return sorted(d.name for d in EXAMPLES_DIR.iterdir() if (d / "wiki").is_dir())


def choose_demo(preselected: str | None, demos: list[str]) -> str:
    if preselected:
        if preselected not in demos:
            die(f"Unknown demo {preselected!r}. Available: {', '.join(demos)}")
        return preselected
    if len(demos) == 1:
        say(f"Demo wiki: {demos[0]} (the only one bundled so far)")
        return demos[0]
    say("Pick a demo wiki to start with:")
    for i, name in enumerate(demos, 1):
        say(f"  {i}) {name}")
    while True:
        reply = ask("Choice", "1")
        if reply.isdigit() and 1 <= int(reply) <= len(demos):
            return demos[int(reply) - 1]
        say("  Please enter a valid number.")


def install_demo(demo: str, wiki_home: Path, *, assume_yes: bool) -> Path:
    wiki_home.mkdir(parents=True, exist_ok=True)
    dest = wiki_home / demo
    if dest.exists():
        if not confirm(
            f"  {dest} already exists — overwrite it?", assume_yes=assume_yes, default=False
        ):
            say("  Keeping the existing demo wiki.")
            return dest
        shutil.rmtree(dest)
    shutil.copytree(EXAMPLES_DIR / demo, dest)
    say(f"  Demo wiki ready at {dest}")
    return dest


# ── Provider wizard ───────────────────────────────────────────────────────────

def choose_provider(preselected: str | None) -> tuple[str, str, str]:
    provider = preselected
    if not provider:
        say("Which LLM provider should the chat use?")
        say("  1) Local Ollama  (free, on-device, nothing leaves your machine)")
        say("  2) Cloud / any OpenAI-compatible endpoint  (OpenRouter, LM Studio, …)")
        provider = "ollama" if ask("Choice", "1") != "2" else "cloud"

    if provider == "ollama":
        return _ollama_wizard()
    if provider == "cloud":
        return _cloud_wizard()
    die(f"Unknown provider {provider!r} (expected 'ollama' or 'cloud').")


def _ollama_wizard() -> tuple[str, str, str]:
    base, key, model = OLLAMA_PRESET
    if shutil.which("ollama") is None:
        say(
            "  ⚠ `ollama` is not on your PATH. Install it from https://ollama.com,\n"
            "    then run `ollama pull llama3.2`. The .env below is preconfigured\n"
            "    for a local Ollama, so chat will work once it's installed."
        )
    else:
        models = _ollama_models()
        if models:
            say(f"  Ollama models found: {', '.join(models)}")
            model = ask("  Model to use for chat", models[0])
        else:
            say(f"  No Ollama models pulled yet. Run:  ollama pull {model}")
            model = ask("  Model to use for chat (once pulled)", model)
    return base, key, model


def _ollama_models() -> list[str]:
    try:
        out = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=10
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    names = []
    for line in out.splitlines()[1:]:  # skip header
        if line.strip():
            names.append(line.split()[0])
    return names


def _cloud_wizard() -> tuple[str, str, str]:
    base_default, _, model_default = CLOUD_PRESET
    base = ask("  Base URL (OpenAI-compatible)", base_default)
    key = getpass.getpass("  API key (input hidden): ").strip() if sys.stdin.isatty() else ask("  API key")
    while not key:
        say("  An API key is required for a cloud provider.")
        key = getpass.getpass("  API key (input hidden): ").strip() if sys.stdin.isatty() else ask("  API key")
    model = ask("  Model id", model_default)
    return base, key, model


# ── .env ──────────────────────────────────────────────────────────────────────

def write_env(wiki_path: Path, wiki_home: Path, provider: tuple[str, str, str], *, assume_yes: bool) -> None:
    env_path = REPO_ROOT / ".env"
    if env_path.exists() and not confirm(
        f"  {env_path} already exists — overwrite it?", assume_yes=assume_yes, default=False
    ):
        say("  Leaving your existing .env untouched.")
        return
    base, key, model = provider
    content = (
        "# Generated by quickstart.py — edit freely.\n"
        f"WIKI_PATH={wiki_path}\n"
        f"WIKI_HOME={wiki_home}\n\n"
        f"LLM_BASE_URL={base}\n"
        f"LLM_API_KEY={key}\n"
        f"LLM_MODEL={model}\n"
    )
    env_path.write_text(content, encoding="utf-8")
    say(f"  Wrote {env_path}")


# ── venv + dependencies ───────────────────────────────────────────────────────

def venv_python(venv_dir: Path) -> Path:
    bindir = "Scripts" if os.name == "nt" else "bin"
    exe = "python.exe" if os.name == "nt" else "python"
    return venv_dir / bindir / exe


def venv_executable(venv_dir: Path, name: str) -> Path:
    bindir = "Scripts" if os.name == "nt" else "bin"
    if os.name == "nt":
        name += ".exe"
    return venv_dir / bindir / name


def create_venv(venv_dir: Path) -> None:
    if venv_python(venv_dir).exists():
        say(f"  Reusing existing venv at {venv_dir}")
        return
    _run([sys.executable, "-m", "venv", str(venv_dir)], "create the virtual environment")


def install_deps(venv_dir: Path) -> None:
    py = str(venv_python(venv_dir))
    _run([py, "-m", "pip", "install", "--upgrade", "pip"], "upgrade pip")
    _run(
        [py, "-m", "pip", "install", "-r", str(REPO_ROOT / "requirements.txt")],
        "install dependencies (this can take a minute)",
    )


def _run(cmd: list[str], what: str) -> None:
    say(f"  → {what} …")
    try:
        subprocess.run(cmd, check=True, cwd=REPO_ROOT)
    except subprocess.CalledProcessError as exc:
        die(f"Failed to {what} (exit {exc.returncode}). Command: {' '.join(cmd)}")


# ── Optional connectivity check ───────────────────────────────────────────────

def check_provider(provider: tuple[str, str, str]) -> None:
    base, key, _ = provider
    url = base.rstrip("/") + "/models"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"} if key else {})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            if resp.status < 400:
                say("  ✓ Provider endpoint reachable.")
                return
    except (urllib.error.URLError, OSError, ValueError):
        pass
    say("  ⚠ Could not reach the provider endpoint yet — that's fine if you "
        "still need to start Ollama / pull a model. Chat will work once it's up.")


# ── Launch ────────────────────────────────────────────────────────────────────

def launch_command(venv_dir: Path, port: int) -> list[str]:
    marimo = venv_executable(venv_dir, "marimo")
    return [str(marimo), "run", "marimo/read_app.py", "--no-sandbox", "--port", str(port)]


def maybe_launch(venv_dir: Path, port: int, *, do_launch: bool, assume_yes: bool) -> None:
    cmd = launch_command(venv_dir, port)
    pretty = " ".join(cmd)
    if not do_launch:
        say("\n\033[32m✓ Setup complete.\033[0m  Launch the read app with:\n")
        say(f"    {pretty}\n")
        return
    if not confirm("\nLaunch the read app now?", assume_yes=assume_yes, default=True):
        say(f"\nWhen ready, run:\n    {pretty}\n")
        return
    say(f"\n  Starting marimo on http://localhost:{port} … (Ctrl-C to stop)\n")
    try:
        subprocess.run(cmd, cwd=REPO_ROOT)
    except KeyboardInterrupt:
        say("\n  Stopped.")


# ── Orchestration ─────────────────────────────────────────────────────────────

def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LLM Wiki quick-start installer.")
    p.add_argument("--demo", help="demo wiki to install (skips the prompt)")
    p.add_argument("--provider", choices=["ollama", "cloud"], help="LLM provider (skips the prompt)")
    p.add_argument("--wiki-home", type=Path, default=DEFAULT_WIKI_HOME, help="where demo wikis are created")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help="port for the read app")
    p.add_argument("--yes", action="store_true", help="assume yes for all confirmations")
    p.add_argument("--no-launch", action="store_true", help="don't offer to launch at the end")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    total = 6

    say("\033[1mLLM Wiki — quick start\033[0m")
    check_python()
    check_repo_layout()

    demos = available_demos()
    if not demos:
        die(f"No demo wikis found under {EXAMPLES_DIR}.")

    step(1, total, "Choose a demo wiki")
    demo = choose_demo(args.demo, demos)

    step(2, total, "Install the demo wiki")
    wiki_home = args.wiki_home.resolve()
    wiki_path = install_demo(demo, wiki_home, assume_yes=args.yes)

    step(3, total, "Configure the LLM provider")
    provider = choose_provider(args.provider)

    step(4, total, "Write .env")
    write_env(wiki_path, wiki_home, provider, assume_yes=args.yes)

    step(5, total, "Create the virtual environment and install dependencies")
    venv_dir = REPO_ROOT / ".venv"
    create_venv(venv_dir)
    install_deps(venv_dir)
    check_provider(provider)

    step(6, total, "Launch")
    maybe_launch(venv_dir, args.port, do_launch=not args.no_launch, assume_yes=args.yes)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        say("\nAborted.")
        raise SystemExit(130)
