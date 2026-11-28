# The installer, explained

This document explains what the installer does and how. You do not need to read it to
install: section 1 is enough. The rest is for checking what runs on the computer before
running it, or for diagnosing a failure.

The installer is four files at the root of the LLM Wiki folder:

| File | System | What it holds |
|---|---|---|
| `install.cmd` | Windows | One command: run `install.ps1` with PowerShell. |
| `install.ps1` | Windows | The installer, in PowerShell. |
| `install.sh` | macOS and Linux | The same installer, in bash. |
| `scripts/install_steps.py` | All | The steps that need Python: the check, the wikis folder, the shortcut and the final message. Both installers call it. |

---

## 1. How to run it

### Windows

1. Download the repository as a zip (on GitHub: **Code → Download ZIP**) and extract it to a
   folder, for example `Documents\llmwiki`.
2. Open that folder in File Explorer.
3. Double-click `install.cmd`. A console window opens and shows what the installer does.
4. Answer the question about the wikis folder (Enter accepts the default).
5. At the end, answer whether to start the web interface now.

Windows may show a blue SmartScreen warning, "Windows protected your PC". SmartScreen warns
about programs downloaded from the internet that Microsoft does not know. Click **More info**,
then **Run anyway**. If that button does not appear, run the installer from PowerShell: in
File Explorer, inside the LLM Wiki folder, type `powershell` in the address bar and press
Enter, then paste:

```
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

### macOS and Linux

1. Download and extract the zip, or clone the repository.
2. Open a terminal in that folder (on macOS: Terminal, then type `cd `, drag the folder onto
   the window and press Enter).
3. Run:

   ```bash
   bash install.sh
   ```

### Options

| `install.sh` | `install.ps1` | Effect |
|---|---|---|
| `--wikis <folder>` | `-Wikis <folder>` | The folder for the wikis; skips the question. |
| `--yes` | `-Yes` | Accept every default and do not start the web interface at the end. |

### What you see at the end

```
Installation complete.
Your wikis are in C:\Users\ana\Documents\LLM Wiki.

1. Start the web interface: double-click open_web.cmd
   It opens http://localhost:8765 in your browser.
2. Open a wiki, then the Settings tab.
3. In "Models", set the chat model and press Test. ...
```

The wikis can be read without a model. The chat needs one: set it in the **Settings** tab,
block "Models", and press **Test** to check it.

If something failed, the last lines name the step and the log:

```
ERROR: the command ended with code 2.
Step 3 (dependencies) failed. The full output is in: C:\Users\ana\Documents\llmwiki\install.log
```

Section 4 says what to do then.

---

## 2. What it installs, and where

Nothing needs administrator rights.

| What | Where | Note |
|---|---|---|
| `uv` | `~/.local/bin/uv` (`C:\Users\<user>\.local\bin\uv.exe`) | A program of Astral that manages Python versions and Python libraries. Installed only when missing, with Astral's official installer. |
| Python 3.12 | The folder where `uv` keeps Python versions, in the user's profile | Downloaded by `uv`. It does not change any other Python on the computer. |
| The libraries | `.venv/` in the LLM Wiki folder | A virtual environment: a Python installation that only LLM Wiki uses. |
| Your wikis | The folder you answered, by default `Documents/LLM Wiki` | The three example wikis are copied there. A wiki already there is never overwritten. |
| `.env` | The LLM Wiki folder | `WIKI_HOME` = your wikis folder. The other lines of an existing `.env` stay. The Settings tab adds the models later. |
| `open_web.cmd` / `open_web.sh` | The LLM Wiki folder | The shortcut that starts the web interface and opens the browser. |
| `install.log` | The LLM Wiki folder | Everything the installer wrote. |

The installer does not install Java, LibreOffice or the local sentence verifier. Reading the
example wikis and chatting need none of them. Ingesting PDF and office documents needs Java
and LibreOffice: see "Prerequisites" in `README.md`.

---

## 3. The steps

| # | Step | What it does | Where |
|---|---|---|---|
| 1 | system data | Writes the system, the shell version and the folder to the log; checks that `pyproject.toml` is beside the script. | shell |
| 2 | uv | Finds `uv` on the PATH or in `~/.local/bin`, `~/.cargo/bin`; if missing, runs Astral's installer in a separate process (`irm https://astral.sh/uv/install.ps1 \| iex` on Windows, `curl -LsSf https://astral.sh/uv/install.sh \| sh` elsewhere). | shell |
| 3 | dependencies | `uv sync --group web`: downloads Python 3.12 and the libraries into `.venv`. | shell |
| 4 | check | `install_steps.py check`: builds the web application, asks it for the list of wikis, and reads the index of each example wiki. No model, no network. | Python |
| 5 | wikis folder | Asks for the folder (the default is the Documents folder plus `LLM Wiki`; on Windows the system's Documents folder, which may be in OneDrive). `install_steps.py wikis` creates it, copies the examples into it and writes `WIKI_HOME` to `.env`. | shell asks, Python copies |
| 6 | shortcut | `install_steps.py finish` writes `open_web.cmd` or `open_web.sh` and prints the final message; the shell then offers to start the web interface. | Python and shell |

The question of step 5 is asked by the shell, not by Python: the shell copies Python's output
to the log, which would hide a question.

On Windows, `install.cmd` exists because a `.ps1` does not run with a double click, and the
default execution policy of PowerShell blocks an unsigned script downloaded from the
internet. `-ExecutionPolicy Bypass` lifts that block for this run only; it does not change
the settings of Windows. `install.ps1` is saved as UTF-8 with a BOM because Windows
PowerShell 5.1 reads a file without one in the old code page of the system.

---

## 4. When a step fails

Open `install.log`, read the last lines and the step they name.

| Step | Usual cause | What to do |
|---|---|---|
| 1 | The installer was run from another folder | Run it from the folder where you extracted LLM Wiki. |
| 2 | No internet connection, or a proxy that blocks `astral.sh` | Install `uv` by hand ([uv's documentation](https://docs.astral.sh/uv/getting-started/installation/)), then run the installer again. |
| 3 | No internet connection, or not enough disk space | Fix it and run the installer again: `uv` reuses what it already downloaded. |
| 4 | The download is incomplete | Download the zip again. |
| 5 | The folder cannot be created (a file with that name, no permission) | Run the installer again and choose another folder. |

Running the installer again is safe: it keeps `uv`, the libraries, the wikis and `.env`.

---

## 5. Moving and uninstalling

**Moving the LLM Wiki folder:** move it, then run the installer again. The shortcut holds
the old path. The wikis folder does not move.

**Uninstalling:**

1. Delete the LLM Wiki folder. That removes the application, its libraries and the shortcut.
2. Your wikis folder stays: delete it only if you do not want your wikis any more.
3. To remove `uv` and the Python versions it downloaded, run `uv self uninstall` if it was
   installed by this installer (see
   [uv's documentation](https://docs.astral.sh/uv/getting-started/installation/#uninstallation)).
