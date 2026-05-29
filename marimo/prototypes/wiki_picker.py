import marimo

__generated_with = "0.23.6"
app = marimo.App(width="full")


@app.cell
def _():
    """Imports, persistence, discovery + helpers.

    No file_browser: directory selection is broken in marimo 0.23.x (GH #1478)
    and the file-mode workaround was ugly. Instead we AUTO-DISCOVER wikis under
    a 'wikis home' folder and merge them with a recent-wikis list into one
    dropdown; a tucked-away text input registers any other / new folder.
    """
    import json
    import os
    from pathlib import Path

    import marimo as mo

    RECENT_FILE = Path.home() / ".llmwiki" / "recent_wikis.json"

    def load_recent() -> list[str]:
        try:
            data = json.loads(RECENT_FILE.read_text())
            return [p for p in data if isinstance(p, str)]
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def save_recent(paths: list[str]) -> None:
        RECENT_FILE.parent.mkdir(parents=True, exist_ok=True)
        RECENT_FILE.write_text(json.dumps(paths, indent=2))

    def push_recent(path: str, existing: list[str], cap: int = 12) -> list[str]:
        updated = [path] + [p for p in existing if p != path]
        save_recent(updated[:cap])
        return updated[:cap]

    def clean_path_input(raw: str) -> str:
        """Strip whitespace and surrounding quotes from a pasted path.

        Handles 'Copy as Pathname' / shell-style paste like '/a/b c' or "/a/b".
        """
        s = raw.strip()
        if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
            s = s[1:-1]
        return s.strip()

    def is_wiki_dir(path: Path) -> bool:
        return (path / "wiki").is_dir() or (path / ".llmwiki").is_dir()

    def discover_wikis(home: Path) -> list[str]:
        """Immediate sub-folders of `home` that look like wikis, plus `home`."""
        found: list[str] = []
        if is_wiki_dir(home):
            found.append(str(home))
        try:
            for child in sorted(home.iterdir()):
                if child.is_dir() and is_wiki_dir(child):
                    found.append(str(child))
        except (FileNotFoundError, PermissionError):
            pass
        return found

    # Resolve env wiki + a "wikis home" to scan (parent of WIKI_PATH, or WIKI_HOME).
    _env = os.environ.get("WIKI_PATH", "")
    _env_path = Path(_env).expanduser().resolve() if _env else None
    _home_env = os.environ.get("WIKI_HOME", "")
    if _home_env:
        WIKI_HOME = Path(_home_env).expanduser().resolve()
    elif _env_path:
        WIKI_HOME = _env_path.parent
    else:
        WIKI_HOME = Path.home()
    ENV_DEFAULT = str(_env_path) if _env_path else ""
    return (
        ENV_DEFAULT,
        Path,
        WIKI_HOME,
        clean_path_input,
        discover_wikis,
        is_wiki_dir,
        load_recent,
        mo,
        push_recent,
    )


@app.cell
def _(ENV_DEFAULT, load_recent, mo):
    """State: active wiki path + recent list (a refresh trigger re-scans discovery)."""
    active_path, set_active_path = mo.state(ENV_DEFAULT or None)
    recent_list, set_recent_list = mo.state(load_recent())
    return active_path, recent_list, set_active_path, set_recent_list


@app.cell
def _(WIKI_HOME, discover_wikis, recent_list):
    """Merge discovered + recent into one ordered, de-duplicated option list."""
    _seen: dict[str, None] = {}
    for _p in [*discover_wikis(WIKI_HOME), *recent_list()]:
        _seen.setdefault(_p, None)
    wiki_options = list(_seen.keys())
    return (wiki_options,)


@app.cell
def _(active_path, mo, set_active_path, wiki_options):
    """The picker — one clean dropdown. Pre-selects the active wiki."""

    def _short(p: str) -> str:
        from pathlib import Path as _P

        parts = _P(p).parts
        return "…/" + "/".join(parts[-2:]) if len(parts) > 2 else p

    switch_dd = mo.ui.dropdown(
        options={_short(p): p for p in wiki_options},
        value=next((_short(p) for p in wiki_options if p == active_path()), None),
        label="Wiki",
        on_change=lambda v: set_active_path(v) if v else None,
    )
    switch_dd
    return


@app.cell
def _(ENV_DEFAULT, mo):
    """Add-a-new-path input, tucked into an accordion so the default view stays clean."""
    new_path = mo.ui.text(
        value="",
        placeholder=ENV_DEFAULT or "/absolute/path/to/wiki",
        full_width=True,
    )
    add_btn = mo.ui.run_button(label="Add")
    add_panel = mo.accordion(
        {"➕ Add another wiki folder": mo.hstack([new_path, add_btn], justify="start")}
    )
    add_panel
    return add_btn, new_path


@app.cell
def _(
    Path,
    add_btn,
    clean_path_input,
    mo,
    new_path,
    push_recent,
    recent_list,
    set_active_path,
    set_recent_list,
):
    """Commit a newly typed path: make active + remember it."""
    _cleaned = clean_path_input(new_path.value)
    if not add_btn.value:
        _msg = mo.md("")
    elif not _cleaned:
        _msg = mo.md("⚠️ Enter a path.")
    else:
        _resolved = str(Path(_cleaned).expanduser().resolve())
        if not Path(_resolved).is_dir():
            _msg = mo.md(f"⚠️ `{_resolved}` is not a directory — not added.")
        else:
            set_active_path(_resolved)
            set_recent_list(push_recent(_resolved, recent_list()))
            _msg = mo.md(f"✅ Added `{_resolved}`")
    _msg
    return


@app.cell
def _(Path, active_path, is_wiki_dir, mo):
    """Active-wiki status line."""
    _ap = active_path()
    if not _ap:
        _status = mo.md("**Active wiki:** _(none — pick one above)_")
    else:
        _p = Path(_ap)
        if not _p.is_dir():
            _status = mo.md(f"**Active wiki:** `{_ap}` — ⚠️ not a directory")
        elif is_wiki_dir(_p):
            _n = len(list((_p / "wiki").rglob("*.md"))) if (_p / "wiki").is_dir() else 0
            _status = mo.md(f"**Active wiki:** `{_ap}` — ✅ {_n} page(s)")
        else:
            _status = mo.md(f"**Active wiki:** `{_ap}` — 🟡 new (no `wiki/` yet)")
    _status
    return


if __name__ == "__main__":
    app.run()
