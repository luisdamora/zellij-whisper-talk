# Spec: One-combo record/insert toggle

Status: **Proposed** (not implemented). Target: a future `v0.2.0`.

## Goal

Collapse the record→insert UX to a single keybinding pressed twice:

```
Ctrl+Shift+y   → recording starts immediately
Ctrl+Shift+y   → recording stops, transcribes, and inserts the text directly
```

This replaces today's four-key flow:

```
Ctrl y → [Idle] Space → [Recording] Space → [Transcribing→Confirming] Enter → insert
```

## Feasibility — verified against zellij-tile / zellij-utils 0.44.3 source

This is achievable with **native Zellij mechanisms** (no hacks). Verified:

- `pipe(&mut self, pipe_message: PipeMessage) -> bool` exists on the `ZellijPlugin`
  trait — `zellij-tile-0.44.3/src/lib.rs:43`.
- The KDL keybind action `MessagePlugin` maps to `Action::KeybindPipe` —
  `zellij-utils-0.44.3/src/kdl/mod.rs:2152`. Its fields include `name`, `plugin`,
  `launch_new`, `floating`, `payload`, `configuration`
  (`zellij-utils-0.44.3/src/input/actions.rs`, `KeybindPipe` variant).
- `MessagePlugin` **launches the plugin if it is not running, and delivers the
  message to the existing instance if it is** (same semantics as `zellij pipe
  --plugin ... (launch if not running)`).

### Why the current mechanism can't do this

The keybind currently uses `LaunchOrFocusPlugin`. On the **second** press it only
re-focuses the floating pane; the plugin receives **no event** telling it "I was
triggered again", so it cannot know to stop. That is why a separate key (`Space`)
is needed to stop today.

A **pipe** fixes this: the message is delivered to the plugin's `pipe()` handler
regardless of focus, and launches the plugin on first use. The combo then works
globally — even when the floating pane is not focused — which is *more* robust
than the focus-dependent model, not less.

## Required changes

### 1. Keybinding (`scripts/install.sh` + generated `config.kdl`)

Replace the `LaunchOrFocusPlugin` bind with a `MessagePlugin` (pipe) bind:

```kdl
bind "Ctrl Shift y" {
    MessagePlugin "file:<PLUGINS_DIR>/zellij_whisper_talk.wasm" {
        name "toggle"
        floating true
        // launch_new false  // reuse the existing instance on the 2nd press
        // forward script_path / model via `configuration { ... }` as today
    }
}
```

The installer's KDL injection (insert-into-existing-`keybinds`-block logic) is
unchanged in shape; only the action node changes.

### 2. Plugin (`src/main.rs`) — implement `pipe()`

Add a `pipe()` method to `impl ZellijPlugin for State` that toggles on the
`"toggle"` pipe name:

- `RecordingState::Idle | Error` → `start_recording()` (request permissions first
  if `!permissions_granted`, exactly as the current Space handler does).
- `RecordingState::Recording` → `stop_recording()`.
- Other states → ignore (debounce while transcribing/inserting).

Keep the existing `Event::Key` handlers (`Space`/`Enter`/`Esc`) as a fallback so
the manual flow still works for users who prefer the preview.

### 3. Auto-insert (skip the preview)

The instant-paste path already exists: `confirm_inject = false` →
`next_state_after_transcription` returns `RecordingState::Done` →
`inject_text_and_close()` runs automatically
(`src/main.rs` — `next_state_after_transcription`, lines ~591 and ~180-188).

For the toggle flow, drive transcription through the `confirm_inject = false`
path so the result is inserted directly. Recommendation: keep `confirm_inject`
configurable so users who want the review/confirm preview can re-enable it.

## Tradeoffs

| Concern | Detail / mitigation |
|---------|---------------------|
| No preview before insert | `inject_text_and_close` uses `zellij action write-chars`, which **types** the text at the prompt — it does **not** press Enter. The user still reviews before executing. |
| Misfire | Background noise → a bad transcription is typed at the cursor; the user clears it manually. `Esc` to cancel while recording remains available. |
| First-ever use | The one-time permission prompt (`RunCommands`, `ChangeApplicationState`, `WriteToStdin`) still appears on first launch. |

## Open questions to resolve at implementation time

- Exact `launch_new` / `floating` combination so the **second** press reuses the
  **same** plugin instance instead of spawning a new pane. The `KeybindPipe`
  variant supports it; confirm the precise flags by running it.
- How `configuration` (script_path, model, api key delivery) is forwarded through
  `MessagePlugin` vs the current `LaunchOrFocusPlugin` configuration block.
- Whether to ship `Ctrl Shift y` as a replacement for `Ctrl y` or as an additional
  binding (keeping the legacy manual flow on a separate key).

## Scope

Small–medium: one new `pipe()` method, one keybind action change in the
installer, flip the confirm default for the pipe path, plus regression tests.
Releasable as `v0.2.0`.

## References (source-of-truth, verified)

- `zellij-tile-0.44.3/src/lib.rs:43` — `ZellijPlugin::pipe`
- `zellij-utils-0.44.3/src/kdl/mod.rs:2152` — `"MessagePlugin" => Action::KeybindPipe`
- `zellij-utils-0.44.3/src/input/actions.rs` — `KeybindPipe` / `CliPipe` fields
- `src/main.rs` — current `RecordingState` machine, `start_recording`,
  `stop_recording`, `inject_text_and_close`, `next_state_after_transcription`
