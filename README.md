# 🎙️ Zellij Whisper Talk

Press a key, speak, and let AI transcribe and inject clean text right at your terminal cursor — all without leaving Zellij.

Records from your microphone, transcribes ultra-fast via OpenRouter (Whisper), then cleans the raw transcription with an LLM (default: `deepseek/deepseek-v4-flash`) before pasting it into your active pane.

> [!NOTE]
> This plugin uses a **hybrid architecture** because Zellij's WASM sandbox has no microphone access. The WASM plugin handles the UI and keyboard events, while a zero-dependency Python host script captures audio and calls the APIs.

## 📸 Demo

<!-- TODO: add a demo GIF to assets/demo.gif -->

## 📋 Requirements

- **Linux** with `arecord` installed (part of `alsa-utils`, preinstalled on most distros)
- **Python 3** (preinstalled on virtually every Linux system)
- An **OpenRouter API key** ([get one here](https://openrouter.ai/keys))

## 🚀 Quick Install

```bash
curl -sSL "https://github.com/luisdamora/zellij-whisper-talk/releases/latest/download/install.sh" | bash
```

The installer will ask for your API key, model, and keybinding — then download, configure, and set up everything.

### Non-interactive / CLI flags

```bash
curl -sSL "https://github.com/luisdamora/zellij-whisper-talk/releases/latest/download/install.sh" | bash -s -- \
  --api-key "sk-or-v1-..." \
  --model "deepseek/deepseek-v4-flash" \
  --keybind "Ctrl y" \
  --non-interactive
```

| Flag               | Default                        | Description                |
| ------------------ | ------------------------------ | -------------------------- |
| `--api-key`         | `$OPENROUTER_API_KEY` env var   | Your OpenRouter API key    |
| `--model`           | `deepseek/deepseek-v4-flash`    | LLM model for text cleanup |
| `--keybind`         | `Ctrl y`                        | Keybinding to trigger      |
| `--config`          | `~/.config/zellij/config.kdl`   | Zellij config path         |
| `--non-interactive` | (prompts)                      | Skip prompts               |
| `-h`, `--help`        | —                              | Show all options           |

### Manual install (no script)

```bash
mkdir -p ~/.config/zellij/plugins

curl -sSL "https://github.com/luisdamora/zellij-whisper-talk/releases/latest/download/zellij_whisper_talk.wasm" \
  -o ~/.config/zellij/plugins/zellij_whisper_talk.wasm

curl -sSL "https://github.com/luisdamora/zellij-whisper-talk/releases/latest/download/transcribe.py" \
  -o ~/.config/zellij/plugins/transcribe.py
chmod +x ~/.config/zellij/plugins/transcribe.py
```

## ⌨️ Keybinding

Add this to your Zellij config (`~/.config/zellij/config.kdl`) inside the `keybinds` block:

```kdl
shared_except "locked" {
    bind "Ctrl y" {
        LaunchOrFocusPlugin "file:~/.config/zellij/plugins/zellij_whisper_talk.wasm" {
            floating true
            script_path "~/.config/zellij/plugins/transcribe.py"
            model "deepseek/deepseek-v4-flash"
            api_key "sk-or-v1-..."   // optional — falls back to $OPENROUTER_API_KEY env var
        }
    }
}
```

> [!TIP]
> If you prefer not to hardcode your API key, export it in your shell config instead:
> ```bash
> export OPENROUTER_API_KEY="sk-or-v1-..."
> ```

## 🎮 How to Use

1. Export your API key:
   ```bash
   export OPENROUTER_API_KEY="sk-or-v1-your-key"
   ```
2. Press your keybinding (e.g. `Ctrl + y`) to open the plugin.
3. Press **Space** to start recording — speak your mind.
4. Press **Space** again to stop. The plugin transcribes, cleans, and pastes the text.
5. Press **Esc** to dismiss the plugin at any time.

## ⚙️ Configuration Options

| Option        | Default                       | Description                                    |
| ------------- | ----------------------------- | ---------------------------------------------- |
| `script_path`  | (required)                    | Absolute path to `transcribe.py`                 |
| `model`        | `deepseek/deepseek-v4-flash`  | OpenRouter model for cleaning the transcription |
| `api_key`      | `$OPENROUTER_API_KEY` env var | Your OpenRouter API key                        |
| `floating`     | `true` (recommended)          | Show as a floating pane                        |

## 🧩 Architecture

```
┌──────────────────────────────┐
│  Zellij WASM Plugin (Rust)   │  UI, key events, permissions, timer
│  zellij_whisper_talk.wasm    │
└──────────────┬───────────────┘
               │ run_command + lock file
               ▼
┌──────────────────────────────┐
│  Host Script (Python 3)      │  arecord → base64 → OpenRouter API
│  transcribe.py               │  Zero external dependencies
└──────────────────────────────┘
```

## 🛠️ Development

```bash
# Build the WASM plugin
cargo build --release --target wasm32-wasip1

# Launch Zellij with the dev layout
zellij --layout dev.kdl
```

## 🔧 Customizing the Cleanup Prompt

Edit `scripts/transcribe.py` and modify the `CLEANUP_SYSTEM_PROMPT` to change how the LLM cleans transcriptions (remove filler words, fix grammar, inject tone, etc.).

You can also change the transcription model by setting the `model` option in the keybinding config.

## 📝 License

This project is provided as open source. See individual files for details.
