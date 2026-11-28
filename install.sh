#!/usr/bin/env bash
# LLM Wiki installer for macOS and Linux. The explanation, step by step, is in INSTALLER.md.
#
# Run it from a terminal, in the folder where you extracted LLM Wiki:
#   bash install.sh
# Options:
#   --wikis <folder>   the folder for your wikis (skips the question)
#   --yes              accept every default and do not start the web interface at the end
#
# Steps: 1 system data, 2 uv, 3 dependencies, 4 check, 5 wikis folder, 6 shortcut.
# On a failure the installer stops and leaves all its output in install.log.

set -uo pipefail
cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"
LOG="$ROOT/install.log"
: > "$LOG"
export NO_COLOR=1 PYTHONUTF8=1
STEP_NUMBER=0
STEP_NAME=""
TOTAL=6
WIKIS=""
ASSUME_YES=0

while [ $# -gt 0 ]; do
  case "$1" in
    --wikis) WIKIS="${2:-}"; shift 2 ;;
    --yes) ASSUME_YES=1; shift ;;
    *) echo "Unknown option: $1 (see the top of install.sh)"; exit 2 ;;
  esac
done

# Write a line to the console and to install.log.
say() {
  printf '%s\n' "$1" | tee -a "$LOG"
}

# Announce a step and remember it for the failure message.
step() {
  STEP_NUMBER=$1
  STEP_NAME=$2
  say ""
  say "== Step $1 of $TOTAL: $2 =="
}

# Stop the installer with the failure message of the current step.
fail() {
  say ""
  say "ERROR: $1"
  say "Step $STEP_NUMBER ($STEP_NAME) failed. The full output is in: $LOG"
  exit 1
}

# Run a program, copy its output to the console and to install.log, and stop the
# installer if the program ends with a code other than 0.
run() {
  say "> $*"
  "$@" 2>&1 | tee -a "$LOG"
  local code=${PIPESTATUS[0]}
  [ "$code" -eq 0 ] || fail "the command ended with code $code."
}

# The default folder for the wikis: the Documents folder of the user, plus "LLM Wiki".
default_wikis() {
  local docs=""
  if command -v xdg-user-dir >/dev/null 2>&1; then
    docs="$(xdg-user-dir DOCUMENTS 2>/dev/null || true)"
    [ "$docs" = "$HOME" ] && docs=""
  fi
  [ -n "$docs" ] || docs="$HOME/Documents"
  printf '%s' "$docs/LLM Wiki"
}

# ── Step 1 ──────────────────────────────────────────────────────────────────────
step 1 "system data"
say "System: $(uname -srm)"
if command -v sw_vers >/dev/null 2>&1; then
  say "macOS: $(sw_vers -productVersion)"
fi
say "LLM Wiki folder: $ROOT"
[ -f "$ROOT/pyproject.toml" ] ||
  fail "pyproject.toml was not found. Run install.sh from the folder where you extracted LLM Wiki."

# ── Step 2 ──────────────────────────────────────────────────────────────────────
step 2 "uv"
CANDIDATES=("$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv")
find_uv() {
  local c
  for c in "${CANDIDATES[@]}"; do
    if [ -x "$c" ]; then printf '%s' "$c"; return; fi
  done
}
UV="$(command -v uv || true)"
[ -n "$UV" ] || UV="$(find_uv)"
if [ -n "$UV" ]; then
  say "uv is already installed: $UV"
else
  say "uv is not installed. Installing it with the official installer of Astral."
  command -v curl >/dev/null 2>&1 || fail "curl is missing; it is needed to download uv."
  run sh -c "curl -LsSf https://astral.sh/uv/install.sh | sh"
  UV="$(find_uv)"
  [ -n "$UV" ] || fail "the uv installer finished, but uv is not in ${CANDIDATES[*]}."
fi
run "$UV" --version

# ── Step 3 ──────────────────────────────────────────────────────────────────────
step 3 "dependencies"
say "uv downloads Python 3.12 and the libraries of LLM Wiki. It can take several minutes."
run "$UV" sync --group web --no-progress

# ── Step 4 ──────────────────────────────────────────────────────────────────────
step 4 "check"
run "$UV" run --group web --no-progress python scripts/install_steps.py check

# ── Step 5 ──────────────────────────────────────────────────────────────────────
step 5 "wikis folder"
if [ -z "$WIKIS" ]; then
  DEFAULT="$(default_wikis)"
  if [ "$ASSUME_YES" -eq 1 ]; then
    WIKIS="$DEFAULT"
  else
    say "Your wikis (documents, pages and index) live in a folder of their own, apart from"
    say "the LLM Wiki folder, so that updating LLM Wiki never touches them."
    read -r -p "Where should your wikis live? [$DEFAULT]: " WIKIS
    [ -n "$WIKIS" ] || WIKIS="$DEFAULT"
  fi
fi
say "Wikis folder: $WIKIS"
run "$UV" run --group web --no-progress python scripts/install_steps.py wikis --folder "$WIKIS"

# ── Step 6 ──────────────────────────────────────────────────────────────────────
step 6 "shortcut"
run "$UV" run --group web --no-progress python scripts/install_steps.py finish --system unix --uv "$UV" --folder "$WIKIS"

if [ "$ASSUME_YES" -eq 0 ]; then
  say ""
  read -r -p "Start the web interface now? [Y/n]: " START
  case "$START" in
    [nN]*) ;;
    *) exec bash "$ROOT/open_web.sh" ;;
  esac
fi
exit 0
