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
command -v arecord >/dev/null 2>&1 || warn "arecord not found. Install alsa-utils for microphone support."
command -v python3 >/dev/null 2>&1 || fail "python3 is required but not found."

success "curl, python3 available"

# ── Interactive prompts ─────────────────────────────────────────────────────
# When run via `curl ... | bash`, the script arrives on stdin (fd 0), so any
# `read` would consume the script's own lines instead of the user's input.
# Reopen stdin from the controlling terminal so prompts read the keyboard.
if $INTERACTIVE && [[ ! -t 0 ]]; then
    if [[ -e /dev/tty ]]; then
        exec < /dev/tty
    else
        warn "No terminal available for prompts — falling back to non-interactive mode."
        INTERACTIVE=false
        [[ -z "$API_KEY" ]] && fail "API key is required. Use --api-key or set OPENROUTER_API_KEY."
    fi
fi

if $INTERACTIVE; then
    section "Configuration"

    echo -e "  Press Enter to accept defaults shown in [brackets].\n"

    if [[ -z "$API_KEY" ]]; then
        read -r -p "  OpenRouter API key: " API_KEY
        if [[ -z "$API_KEY" ]]; then
            fail "API key is required. Get one at https://openrouter.ai/keys"
        fi
    else
        echo -e "  OpenRouter API key: ${GREEN}[detected from environment]${RESET}"
    fi

    read -r -p "  LLM model for text cleanup [$MODEL]: " MODEL_INPUT
    MODEL="${MODEL_INPUT:-$MODEL}"

    read -r -p "  Keybinding to trigger plugin [$KEYBIND]: " KEYBIND_INPUT
    KEYBIND="${KEYBIND_INPUT:-$KEYBIND}"

    echo ""
    echo -e "  ${BOLD}Summary:${RESET}"
    echo -e "    API Key:    ${GREEN}$(echo "$API_KEY" | cut -c1-12)...${RESET}"
    echo -e "    Model:      ${CYAN}$MODEL${RESET}"
    echo -e "    Keybinding: ${CYAN}$KEYBIND${RESET}"
    echo ""

    read -r -p "  Proceed with installation? [Y/n] " CONFIRM
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

echo "  Downloading WASM plugin..."
curl -sSL "${RELEASE_URL}/zellij_whisper_talk.wasm" -o "${PLUGINS_DIR}/zellij_whisper_talk.wasm"
success "zellij_whisper_talk.wasm"

echo "  Downloading host script..."
curl -sSL "${RELEASE_URL}/transcribe.py" -o "${PLUGINS_DIR}/transcribe.py"
chmod +x "${PLUGINS_DIR}/transcribe.py"
success "transcribe.py (executable)"

# ── Configure Zellij ────────────────────────────────────────────────────────
section "Configuring Zellij"

mkdir -p "$ZELLIJ_DIR"

# Self-contained keybinds block. Zellij merges multiple top-level `keybinds {}`
# blocks, and `shared_except` MUST be nested inside one — so we always wrap it,
# both when appending to an existing config and when creating a fresh one.
KEYBINDS_BLOCK=$(cat <<KDL

keybinds {
    shared_except "locked" {
        bind "${KEYBIND}" {
            LaunchOrFocusPlugin "file:${PLUGINS_DIR}/zellij_whisper_talk.wasm" {
                floating true
                script_path "${PLUGINS_DIR}/transcribe.py"
                model "${MODEL}"
            }
        }
    }
}
KDL
)

if [[ -f "$CONFIG_FILE" ]]; then
    # Backup existing config
    BACKUP="${CONFIG_FILE}.backup.$(date +%s)"
    cp "$CONFIG_FILE" "$BACKUP"

    # Check if plugin is already configured
    if grep -q "zellij_whisper_talk.wasm" "$CONFIG_FILE" 2>/dev/null; then
        warn "Plugin already configured in $CONFIG_FILE — skipping config injection."
        echo "  Backup saved to: $BACKUP"
    else
        echo "$KEYBINDS_BLOCK" >> "$CONFIG_FILE"
        success "Keybinding appended to $CONFIG_FILE"
        echo "  Backup saved to: $BACKUP"
    fi
else
    # Create fresh config
    echo "$KEYBINDS_BLOCK" > "$CONFIG_FILE"
    success "Created new config at $CONFIG_FILE"
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
