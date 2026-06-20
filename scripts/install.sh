#!/usr/bin/env bash
set -euo pipefail

# ─── Zellij Whisper Talk — One-step installer ───────────────────────────────
# Usage:
#   curl ... | bash                          # interactive
#   bash install.sh --api-key sk-...         # non-interactive with flags
#   bash install.sh --help                   # show all options
# ────────────────────────────────────────────────────────────────────────────

REPO="luisdamora/zellij-whisper-talk"
RELEASE_URL="https://github.com/$REPO/releases/latest/download"
PLUGINS_DIR="${HOME}/.config/zellij/plugins"
CONFIG_FILE="${HOME}/.config/zellij/config.kdl"
ZELLIJ_DIR="${HOME}/.config/zellij"

# ── Defaults ────────────────────────────────────────────────────────────────
API_KEY="${OPENROUTER_API_KEY:-}"
MODEL="deepseek/deepseek-v4-flash"
KEYBIND="Ctrl y"

# ── Colors ──────────────────────────────────────────────────────────────────
BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
CYAN="\033[36m"
RED="\033[31m"
RESET="\033[0m"

# ── Parse args ──────────────────────────────────────────────────────────────
INTERACTIVE=true
while [[ $# -gt 0 ]]; do
    case "$1" in
        --api-key)       API_KEY="$2"; shift 2 ;;
        --model)         MODEL="$2"; shift 2 ;;
        --keybind)       KEYBIND="$2"; shift 2 ;;
        --plugins-dir)   PLUGINS_DIR="$2"; shift 2 ;;
        --config)        CONFIG_FILE="$2"; shift 2 ;;
        --non-interactive) INTERACTIVE=false; shift ;;
        -h|--help)
            echo "Zellij Whisper Talk — One-step installer"
            echo ""
            echo "Usage:"
            echo "  bash install.sh                          # interactive mode"
            echo "  bash install.sh --api-key sk-...         # non-interactive"
            echo ""
            echo "Options:"
            echo "  --api-key KEY       OpenRouter API key"
            echo "  --model MODEL       LLM model for cleanup (default: deepseek/deepseek-v4-flash)"
            echo "  --keybind KEY       Keybinding to trigger plugin (default: Ctrl y)"
            echo "  --plugins-dir DIR   Plugin install directory (default: ~/.config/zellij/plugins)"
            echo "  --config FILE       Zellij config path (default: ~/.config/zellij/config.kdl)"
            echo "  --non-interactive   Skip prompts, use defaults for missing values"
            echo "  -h, --help          Show this help"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ── Helper functions ────────────────────────────────────────────────────────
section()  { echo -e "\n${BOLD}${CYAN}═══ $1 ═══${RESET}"; }
success() { echo -e "  ${GREEN}✓${RESET} $1"; }
warn()    { echo -e "  ${YELLOW}⚠${RESET} $1"; }
fail()    { echo -e "  ${RED}✗${RESET} $1"; exit 1; }

# ── Prerequisites check ─────────────────────────────────────────────────────
section "Checking prerequisites"

command -v curl  >/dev/null 2>&1 || fail "curl is required but not found."
command -v tar   >/dev/null 2>&1 || fail "tar is required but not found."
command -v python3 >/dev/null 2>&1 || fail "python3 is required but not found."
command -v arecord >/dev/null 2>&1 || command -v pw-record >/dev/null 2>&1 || command -v parec >/dev/null 2>&1 \
    || warn "No audio recorder found. Install pipewire (pw-record), pulseaudio (parec), or alsa-utils (arecord)."

success "curl, tar, python3 available"

# ── Interactive prompts ─────────────────────────────────────────────────────
# When run via `curl ... | bash`, the script itself is on stdin (fd 0) and bash
# keeps reading it from there. So we must NOT touch fd 0 — instead each `read`
# pulls from the controlling terminal /dev/tty directly. Reading from /dev/tty
# also works when the script is launched normally (stdin already a TTY).
# Probe by actually opening /dev/tty: the file can exist yet fail to open
# (ENXIO) when the process has no controlling terminal, so `-r` is not enough.
if $INTERACTIVE && ! { true < /dev/tty; } 2>/dev/null; then
    warn "No terminal available for prompts — falling back to non-interactive mode."
    INTERACTIVE=false
    [[ -z "$API_KEY" ]] && fail "API key is required. Use --api-key or set OPENROUTER_API_KEY."
fi

if $INTERACTIVE; then
    section "Configuration"

    echo -e "  Press Enter to accept defaults shown in [brackets].\n"

    if [[ -z "$API_KEY" ]]; then
        read -r -p "  OpenRouter API key: " API_KEY < /dev/tty
        if [[ -z "$API_KEY" ]]; then
            fail "API key is required. Get one at https://openrouter.ai/keys"
        fi
    else
        echo -e "  OpenRouter API key: ${GREEN}[detected from environment]${RESET}"
    fi

    read -r -p "  LLM model for text cleanup [$MODEL]: " MODEL_INPUT < /dev/tty
    MODEL="${MODEL_INPUT:-$MODEL}"

    read -r -p "  Keybinding to trigger plugin [$KEYBIND]: " KEYBIND_INPUT < /dev/tty
    KEYBIND="${KEYBIND_INPUT:-$KEYBIND}"

    echo ""
    echo -e "  ${BOLD}Summary:${RESET}"
    echo -e "    API Key:    ${GREEN}$(echo "$API_KEY" | cut -c1-12)...${RESET}"
    echo -e "    Model:      ${CYAN}$MODEL${RESET}"
    echo -e "    Keybinding: ${CYAN}$KEYBIND${RESET}"
    echo ""

    read -r -p "  Proceed with installation? [Y/n] " CONFIRM < /dev/tty
    if [[ "$CONFIRM" =~ ^[Nn] ]]; then
        echo "  Aborted."
        exit 0
    fi
elif [[ -z "$API_KEY" ]]; then
    fail "API key is required in non-interactive mode. Use --api-key or set OPENROUTER_API_KEY."
fi

# ── Download plugin files ───────────────────────────────────────────────────
section "Downloading plugin"

mkdir -p "$PLUGINS_DIR"

# -f makes curl FAIL on HTTP errors (404/5xx) instead of silently writing the
# error page body ("Not Found") into the destination file.
echo "  Downloading WASM plugin..."
curl -fsSL "${RELEASE_URL}/zellij_whisper_talk.wasm" -o "${PLUGINS_DIR}/zellij_whisper_talk.wasm" \
    || fail "Failed to download the WASM plugin (HTTP error). The release may be incomplete."

# Verify it's a real WebAssembly module (magic '\0asm' = 00 61 73 6d), not an
# HTML/'Not Found' page that a non-failing download would have saved verbatim.
WASM_MAGIC=$(head -c 4 "${PLUGINS_DIR}/zellij_whisper_talk.wasm" | od -An -tx1 | tr -d ' \n')
[[ "$WASM_MAGIC" == "0061736d" ]] \
    || fail "Downloaded WASM is invalid (magic: ${WASM_MAGIC:-empty}). The release asset may be missing."
success "zellij_whisper_talk.wasm (verified)"

# The sidecar is a multi-file Python package (transcribe.py + whisper_sidecar/),
# shipped as one tarball so it stays atomic and future-proof.
echo "  Downloading host sidecar..."
SIDECAR_TARBALL="$(mktemp)"
curl -fsSL "${RELEASE_URL}/zellij_whisper_talk_sidecar.tar.gz" -o "$SIDECAR_TARBALL" \
    || fail "Failed to download the sidecar package (HTTP error)."
tar -xzf "$SIDECAR_TARBALL" -C "$PLUGINS_DIR" || fail "Failed to extract the sidecar package."
rm -f "$SIDECAR_TARBALL"
chmod +x "${PLUGINS_DIR}/transcribe.py" 2>/dev/null || true
[[ -f "${PLUGINS_DIR}/whisper_sidecar/config.py" ]] \
    || fail "Sidecar package incomplete after extraction (missing whisper_sidecar/)."
success "transcribe.py + whisper_sidecar/ (extracted)"

# ── Configure Zellij ────────────────────────────────────────────────────────
section "Configuring Zellij"

mkdir -p "$ZELLIJ_DIR"

# Zellij processes only the FIRST top-level `keybinds {}` block and silently
# ignores any later ones — so a second appended block would never bind. The
# `shared_except` entry must be nested INSIDE the existing keybinds block.
# INNER_BLOCK is that entry; WRAPPED_BLOCK adds the keybinds wrapper for a fresh
# config (or a config that has no keybinds block yet).
INNER_BLOCK=$(cat <<KDL
    shared_except "locked" {
        bind "${KEYBIND}" {
            LaunchOrFocusPlugin "file:${PLUGINS_DIR}/zellij_whisper_talk.wasm" {
                floating true
                script_path "${PLUGINS_DIR}/transcribe.py"
                model "${MODEL}"
            }
        }
    }
KDL
)

WRAPPED_BLOCK=$(cat <<KDL

keybinds {
${INNER_BLOCK}
}
KDL
)

if [[ -f "$CONFIG_FILE" ]] && grep -q "zellij_whisper_talk.wasm" "$CONFIG_FILE" 2>/dev/null; then
    warn "Plugin already configured in $CONFIG_FILE — skipping config injection."
else
    if [[ -f "$CONFIG_FILE" ]]; then
        BACKUP="${CONFIG_FILE}.backup.$(date +%s)"
        cp "$CONFIG_FILE" "$BACKUP"
    fi

    # Insert into an existing keybinds block if present; otherwise create/append
    # a wrapped block. python3 is a hard prerequisite, so KDL editing is robust.
    RESULT=$(INNER_BLOCK="$INNER_BLOCK" WRAPPED_BLOCK="$WRAPPED_BLOCK" \
        python3 - "$CONFIG_FILE" <<'PY'
import os, re, sys

path = sys.argv[1]
inner = os.environ["INNER_BLOCK"].rstrip("\n") + "\n"
wrapped = os.environ["WRAPPED_BLOCK"].rstrip("\n") + "\n"

try:
    with open(path) as fh:
        lines = fh.readlines()
except FileNotFoundError:
    lines = []

out, inserted = [], False
for line in lines:
    out.append(line)
    # First top-level `keybinds {` (or `keybinds clear-defaults=true {`) opener.
    if not inserted and re.match(r"\s*keybinds\b.*\{\s*$", line):
        out.append(inner)
        inserted = True

if not inserted:
    if out and not out[-1].endswith("\n"):
        out.append("\n")
    out.append(wrapped)

with open(path, "w") as fh:
    fh.writelines(out)

print("inserted" if inserted else "created")
PY
    )

    if [[ "$RESULT" == "inserted" ]]; then
        success "Keybinding inserted into existing keybinds block in $CONFIG_FILE"
    else
        success "Keybinding written to $CONFIG_FILE"
    fi
    [[ -n "${BACKUP:-}" ]] && echo "  Backup saved to: $BACKUP"
fi

# ── Environment variable hint ────────────────────────────────────────────────
section "Almost done!"

echo -e "  Add this to your shell config (${CYAN}~/.bashrc${RESET} or ${CYAN}~/.zshrc${RESET}):"
echo ""
echo -e "    ${BOLD}export OPENROUTER_API_KEY=\"${API_KEY}\"${RESET}"
echo ""
echo -e "  Then restart Zellij and press ${BOLD}${KEYBIND}${RESET} to start talking."
echo ""
echo -e "  ${GREEN}${BOLD}🎙️  Happy dictating!${RESET}"
