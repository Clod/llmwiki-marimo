"""The steps of the installer that need Python: `install.sh` and `install.ps1` call them
after `uv sync`. The explanation of the whole installer is in `INSTALLER.md`.

    python scripts/install_steps.py check
    python scripts/install_steps.py wikis --folder <folder>
    python scripts/install_steps.py finish --system windows|unix --uv <path of uv> --folder <folder>

`check` is the smoke test: it builds the web application, asks it for the wiki picker,
and reads the index of each example wiki. It calls no model and uses no network.
`wikis` creates the wikis folder, copies the example wikis into it (never over a wiki
already there) and writes `WIKI_HOME` to `.env`. `finish` writes the `open_web` shortcut
and prints the final message. Each command exits with 1 and a message on failure.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _path in (ROOT / "base", ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

PORT = 8765


class InstallError(RuntimeError):
    """A step failed; the message says what to do."""


# ── check ─────────────────────────────────────────────────────────────────────

def check(root: Path = ROOT) -> list[str]:
    """The smoke test. Returns the lines to print; raises `InstallError` on a failure."""
    from fastapi.testclient import TestClient

    from domain.wiki_registry import example_wikis
    from services.ingest import peek_stats
    from web.app import create_app
    from web.settings import WebSettings

    examples = example_wikis(root / "examples")
    if not examples:
        raise InstallError(f"no example wiki in {root / 'examples'}; the download looks incomplete.")
    lines = []
    for example in examples:
        stats = peek_stats(example / ".llmwiki" / "index.db")
        if not stats:
            raise InstallError(f"the index of the example wiki {example.name} cannot be read.")
        lines.append(f"Example wiki {example.name}: {stats['sources']} sources, {stats['pages']} pages.")
    with tempfile.TemporaryDirectory() as scratch:
        settings = WebSettings(wiki_path=None, wiki_home=Path(scratch), recent_file=Path(scratch) / "recent.json",
                               examples_dir=root / "examples")
        response = TestClient(create_app(settings)).get("/")
        if response.status_code != 200 or any(e.name not in response.text for e in examples):
            raise InstallError(f"the web interface did not answer as expected (HTTP {response.status_code}).")
    lines.append("The web interface answers and lists the example wikis.")
    return lines


# ── wikis ─────────────────────────────────────────────────────────────────────

def resolve_folder(raw: str, home: Path | None = None) -> Path:
    """The wikis folder an answer names: `~` expanded, a relative answer under the home
    folder, quotes from a pasted path removed."""
    from domain.wiki_registry import clean_path_input

    text = clean_path_input(raw)
    if not text:
        raise InstallError("no folder was given for the wikis.")
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = (home or Path.home()) / path
    return path.resolve()


def prepare_wikis(folder: Path, root: Path = ROOT, env_file: Path | None = None) -> list[str]:
    """Create `folder`, copy each example wiki into it unless a wiki is already there,
    and write `WIKI_HOME=<folder>` to `.env` (the other lines of `.env` stay)."""
    from domain.wiki_registry import copy_destination, example_wikis
    from services.wiki import copy_example
    from web.settings import write_models

    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise InstallError(f"the folder {folder} cannot be created: {exc}") from exc
    lines = [f"Wikis folder: {folder}"]
    for example in example_wikis(root / "examples"):
        dest, exists = copy_destination(example, folder)
        if exists:
            lines.append(f"Kept the wiki already in {dest}.")
        else:
            copy_example(example, folder)
            lines.append(f"Copied the example wiki {example.name} to {dest}.")
    env_file = env_file or root / ".env"
    write_models(env_file, {"WIKI_HOME": str(folder)})
    lines.append(f"Wrote WIKI_HOME to {env_file}.")
    return lines


# ── finish ────────────────────────────────────────────────────────────────────

def shortcut_text(system: str, uv: str, profile: str | None = None) -> str:
    """The content of `open_web.cmd` (Windows) or `open_web.sh` (macOS, Linux).

    `cmd.exe` reads a `.cmd` in the code page of the console, so the Windows file names
    the user's profile folder as `%USERPROFILE%`, not by its letters (a user name with
    accents would break the path), and uses the `uv` of the PATH when there is one."""
    if system == "windows":
        profile = profile if profile is not None else os.environ.get("USERPROFILE", "")
        if profile and uv.lower().startswith(profile.lower()):
            uv = "%USERPROFILE%" + uv[len(profile):]
        return ("@echo off\r\n"
                "rem Starts the LLM Wiki web interface and opens the browser. Written by install.ps1.\r\n"
                "cd /d \"%~dp0\"\r\n"
                "set PYTHONUTF8=1\r\n"
                "set \"UV=uv\"\r\n"
                f"where uv >nul 2>nul || set \"UV={uv}\"\r\n"
                "\"%UV%\" run --group web python scripts\\open_web.py\r\n"
                "pause\r\n")
    return ("#!/usr/bin/env bash\n"
            "# Starts the LLM Wiki web interface and opens the browser. Written by install.sh.\n"
            "cd \"$(dirname \"$0\")\" || exit 1\n"
            "export PYTHONUTF8=1\n"
            f"UV=\"$(command -v uv || echo '{uv}')\"\n"
            "exec \"$UV\" run --group web python scripts/open_web.py\n")


def write_shortcut(system: str, uv: str, root: Path = ROOT, profile: str | None = None) -> Path:
    path = root / ("open_web.cmd" if system == "windows" else "open_web.sh")
    path.write_bytes(shortcut_text(system, uv, profile).encode("utf-8"))
    if system != "windows":
        path.chmod(0o755)
    return path


def final_message(system: str, folder: Path) -> list[str]:
    """The last lines of the installer: how to start, and where to set the model."""
    start = "double-click open_web.cmd" if system == "windows" else "run bash open_web.sh"
    return [
        "Installation complete.",
        f"Your wikis are in {folder}.",
        "",
        f"1. Start the web interface: {start}",
        f"   It opens http://localhost:{PORT} in your browser.",
        "2. Open a wiki, then the Settings tab.",
        "3. In \"Models\", set the chat model and press Test. The ingestion and vision models",
        "   are optional: each falls back to the chat model.",
        "Without a model you can read the wikis, but the chat cannot answer.",
        "To ingest PDF and office documents you also need Java and LibreOffice: see",
        "\"Prerequisites\" in README.md.",
    ]


# ── command line ──────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    wikis = sub.add_parser("wikis")
    wikis.add_argument("--folder", required=True)
    finish = sub.add_parser("finish")
    finish.add_argument("--system", choices=["windows", "unix"], required=True)
    finish.add_argument("--uv", required=True)
    finish.add_argument("--folder", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            lines = check()
        elif args.command == "wikis":
            lines = prepare_wikis(resolve_folder(args.folder))
        else:
            path = write_shortcut(args.system, args.uv)
            lines = [f"Created: {path}", "", *final_message(args.system, resolve_folder(args.folder))]
    except InstallError as exc:
        print(f"ERROR: {exc}")
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
