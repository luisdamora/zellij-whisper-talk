use std::collections::BTreeMap;
use zellij_tile::prelude::*;

// Pure leaf modules (no zellij-tile dependency). Unit-tested.
mod config;
mod sanitize;

// ANSI Escape Codes for premium styling
const COLOR_RESET: &str = "\x1b[0m";
const COLOR_BOLD: &str = "\x1b[1m";

// Theme colors (using 256-color palette for smoother colors)
const COLOR_BORDER: &str = "\x1b[38;5;63m";     // Premium Indigo/Blue
const COLOR_TITLE: &str = "\x1b[38;5;159m";     // Soft Cyan/White
const COLOR_MUTED: &str = "\x1b[38;5;244m";     // Slate Gray
const COLOR_RECORDING: &str = "\x1b[38;5;203m"; // Rose Red
const COLOR_PROCESSING: &str = "\x1b[38;5;215m"; // Warm Orange
const COLOR_SUCCESS: &str = "\x1b[38;5;120m";   // Pastel Green
const COLOR_ERROR: &str = "\x1b[38;5;203m";     // Rose Red for errors

const WAVE_FRAMES: &[&str] = &[
    " ▂▃▅▆▇█▇▆▅▃▂ ",
    "▂▃▅▆▇███▇▆▅▃▂",
    "▃▅▆▇█████▇▆▅▃",
    "▅▆▇███████▇▆▅",
    "▃▅▆▇█████▇▆▅▃",
    "▂▃▅▆▇███▇▆▅▃▂",
    " ▂▃▅▆▇█▇▆▅▃▂ ",
    "  ▂▃▅▆▇▆▅▃▂  ",
    "   ▂▂▃▅▃▂▂   ",
    "     ▂▃▂     ",
    "      ▂      ",
    "             ",
];

const SPINNER_FRAMES: &[&str] = &["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

#[derive(Default)]
struct State {
    recording_state: RecordingState,
    transcription_text: String,
    error_message: String,
    // Configuration (resolved in load() via PluginConfig::from_btreemap).
    config: config::PluginConfig,
    initialized: bool,
    permissions_granted: bool,
    // Animation & timing
    animation_tick: usize,
    seconds_elapsed: f32,
    timer_active: bool,
    // Unique ID
    plugin_id: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum RecordingState {
    Idle,
    Recording,
    Transcribing,
    Confirming,
    Done,
    Error,
}

impl Default for RecordingState {
    fn default() -> Self {
        RecordingState::Idle
    }
}

register_plugin!(State);

impl ZellijPlugin for State {
    fn load(&mut self, configuration: BTreeMap<String, String>) {
        eprintln!("Zellij Whisper Talk: load() started");

        // Subscribe to events
        subscribe(&[
            EventType::Key,
            EventType::RunCommandResult,
            EventType::PermissionRequestResult,
            EventType::Timer,
        ]);

        // Read configuration through the pure parser (all keys optional,
        // backward compatible).
        self.config = config::PluginConfig::from_btreemap(&configuration);

        self.plugin_id = get_plugin_ids().plugin_id;
        self.initialized = true;
        eprintln!(
            "Zellij Whisper Talk: load() complete. Initialized: {}, script_path: {}, plugin_id: {}, confirm_inject: {}",
            self.initialized, self.config.script_path, self.plugin_id, self.config.confirm_inject
        );
    }

    fn update(&mut self, event: Event) -> bool {
        eprintln!("Zellij Whisper Talk: update() called with event: {:?}", event);
        let mut should_render = false;

        match event {
            Event::Key(key) => {
                match key.bare_key {
                    // Enter in Confirming confirms injection (spec injection-confirmation).
                    BareKey::Enter if self.recording_state == RecordingState::Confirming => {
                        self.inject_text_and_close();
                        should_render = true;
                    }
                    BareKey::Char(' ') | BareKey::Enter => {
                        match self.recording_state {
                            RecordingState::Idle | RecordingState::Error => {
                                if self.permissions_granted {
                                    self.start_recording();
                                } else {
                                    eprintln!("Zellij Whisper Talk: Requesting permissions on user trigger");
                                    request_permission(&[
                                        PermissionType::RunCommands,
                                        PermissionType::ChangeApplicationState,
                                        PermissionType::WriteToStdin,
                                    ]);
                                }
                                should_render = true;
                            }
                            RecordingState::Recording => {
                                // Stop recording
                                self.stop_recording();
                                should_render = true;
                            }
                            _ => {}
                        }
                    }
                    BareKey::Esc => {
                        self.timer_active = false;
                        match self.recording_state {
                            RecordingState::Recording => {
                                // Stop the recorder by removing the lock file.
                                let lock_file = lock_file_path(self.plugin_id);
                                run_command(&["rm", "-f", &lock_file], BTreeMap::new());
                            }
                            RecordingState::Confirming => {
                                // Spec: do NOT inject; remove the temp text file; close.
                                let text_file = text_file_path(self.plugin_id);
                                run_command(&["rm", "-f", &text_file], BTreeMap::new());
                            }
                            _ => {}
                        }
                        close_self();
                    }
                    _ => {}
                }
            }
            Event::Timer(_seconds) => {
                if self.timer_active {
                    self.animation_tick += 1;
                    self.seconds_elapsed += 0.1;
                    should_render = true;

                    match self.recording_state {
                        RecordingState::Recording => {
                            // Watchdog (spec recording-lifecycle): auto-stop at the cap.
                            if recording_exceeded_max_duration(
                                self.seconds_elapsed,
                                self.config.max_duration,
                            ) {
                                eprintln!(
                                    "Zellij Whisper Talk: max duration {}s reached, auto-stopping",
                                    self.config.max_duration
                                );
                                self.error_message = format!(
                                    "Max duration ({}s) reached — stopping",
                                    self.config.max_duration
                                );
                                self.stop_recording();
                            }
                            set_timeout(0.1);
                        }
                        RecordingState::Transcribing => {
                            set_timeout(0.1);
                        }
                        RecordingState::Done => {
                            // Legacy instant-paste path (confirm_inject=false):
                            // brief display, then inject + close.
                            if self.seconds_elapsed >= 1.2 {
                                self.inject_text_and_close();
                            } else {
                                set_timeout(0.1);
                            }
                        }
                        RecordingState::Confirming => {
                            // Wait for Enter/Esc — no timer-driven action.
                            self.timer_active = false;
                        }
                        _ => {
                            self.timer_active = false;
                        }
                    }
                }
            }
            Event::RunCommandResult(exit_code, stdout, stderr, context) => {
                if context.get("action").map(|s| s.as_str()) == Some("transcribe") {
                    if exit_code == Some(0) {
                        let text = String::from_utf8_lossy(&stdout).trim().to_string();
                        if !text.is_empty() {
                            // Defense-in-depth: the sidecar already sanitized,
                            // but apply the Rust filter too before any display/inject.
                            self.transcription_text = sanitize::sanitize_terminal_text(&text);
                            self.error_message.clear();
                            // Spec injection-confirmation: gate on confirm_inject.
                            self.recording_state =
                                next_state_after_transcription(self.config.confirm_inject);
                            self.seconds_elapsed = 0.0;
                            match self.recording_state {
                                // Legacy instant-paste path keeps the timer.
                                RecordingState::Done => {
                                    self.timer_active = true;
                                    set_timeout(0.1);
                                }
                                // Confirming waits for Enter/Esc — no timer needed.
                                RecordingState::Confirming => {
                                    self.timer_active = false;
                                }
                                _ => {}
                            }
                        } else {
                            self.error_message = "Transcription was empty".to_string();
                            self.recording_state = RecordingState::Error;
                            self.timer_active = false;
                        }
                    } else {
                        let err = String::from_utf8_lossy(&stderr).trim().to_string();
                        self.error_message = if err.is_empty() {
                            "Transcription command failed".to_string()
                        } else {
                            err
                        };
                        self.recording_state = RecordingState::Error;
                        self.timer_active = false;
                    }
                    should_render = true;
                }
            }
            Event::PermissionRequestResult(result) => {
                eprintln!("Zellij Whisper Talk: Permission result callback: {:?}", result);
                if result == PermissionStatus::Granted {
                    self.permissions_granted = true;
                    self.start_recording();
                } else {
                    self.error_message = "Permissions denied".to_string();
                    self.recording_state = RecordingState::Error;
                    self.timer_active = false;
                }
                should_render = true;
            }
            _ => {}
        }

        should_render
    }

    fn render(&mut self, rows: usize, cols: usize) {
        if !self.initialized {
            println!("Initializing plugin...");
            return;
        }

        let width = 46;
        let height = 7;

        if cols < width || rows < height {
            println!("Terminal too small!");
            return;
        }

        let pad_x = (cols - width) / 2;
        let pad_y = (rows - height) / 2;

        // Print vertical padding
        for _ in 0..pad_y {
            println!();
        }

        let margin = " ".repeat(pad_x);

        // Top Border
        println!("{}{ColorBorder}┌──────────────────────────────────────────┐{ColorReset}", margin, ColorBorder = COLOR_BORDER, ColorReset = COLOR_RESET);
        
        // Header with centered title
        println!("{}{ColorBorder}│{ColorReset}           {ColorTitle}{ColorBold}🎙️  ZELLIJ WHISPER TALK{ColorReset}           {ColorBorder}│{ColorReset}", margin, ColorBorder = COLOR_BORDER, ColorReset = COLOR_RESET, ColorTitle = COLOR_TITLE, ColorBold = COLOR_BOLD);
        println!("{}{ColorBorder}├──────────────────────────────────────────┤{ColorReset}", margin, ColorBorder = COLOR_BORDER, ColorReset = COLOR_RESET);

        // Content Line 1 (Spacing / Context info)
        match self.recording_state {
            RecordingState::Recording => {
                // Show elapsed timer
                let minutes = (self.seconds_elapsed as u32) / 60;
                let seconds = (self.seconds_elapsed as u32) % 60;
                let timer_str = format!("{:02}:{:02}", minutes, seconds);
                println!(
                    "{}{ColorBorder}│{ColorReset}          {ColorRecord}{ColorBold}🔴 Recording... [ {} ]{ColorReset}          {ColorBorder}│{ColorReset}",
                    margin,
                    timer_str,
                    ColorBorder = COLOR_BORDER,
                    ColorReset = COLOR_RESET,
                    ColorRecord = COLOR_RECORDING,
                    ColorBold = COLOR_BOLD
                );
            }
            RecordingState::Transcribing => {
                let spin_idx = self.animation_tick % SPINNER_FRAMES.len();
                let spin = SPINNER_FRAMES[spin_idx];
                println!(
                    "{}{ColorBorder}│{ColorReset}            {ColorProc}⏳ Processing... [ {} ]{ColorReset}            {ColorBorder}│{ColorReset}",
                    margin,
                    spin,
                    ColorBorder = COLOR_BORDER,
                    ColorReset = COLOR_RESET,
                    ColorProc = COLOR_PROCESSING
                );
            }
            RecordingState::Done => {
                println!(
                    "{}{ColorBorder}│{ColorReset}             {ColorSuccess}{ColorBold}✅ Done! Pasting...{ColorReset}             {ColorBorder}│{ColorReset}",
                    margin,
                    ColorBorder = COLOR_BORDER,
                    ColorReset = COLOR_RESET,
                    ColorSuccess = COLOR_SUCCESS,
                    ColorBold = COLOR_BOLD
                );
            }
            RecordingState::Confirming => {
                println!(
                    "{}{ColorBorder}│{ColorReset}          {ColorSuccess}{ColorBold}✅ Ready! Review the text{ColorReset}          {ColorBorder}│{ColorReset}",
                    margin,
                    ColorBorder = COLOR_BORDER,
                    ColorReset = COLOR_RESET,
                    ColorSuccess = COLOR_SUCCESS,
                    ColorBold = COLOR_BOLD
                );
            }
            RecordingState::Error => {
                let err_trimmed = if self.error_message.chars().count() > 30 {
                    format!("{}...", self.error_message.chars().take(27).collect::<String>())
                } else {
                    self.error_message.clone()
                };
                let err_len = err_trimmed.chars().count() + 9;
                let left_pad = (44 - err_len) / 2;
                let right_pad = 44 - err_len - left_pad;
                println!(
                    "{}{ColorBorder}│{ColorReset}{}{ColorError}{ColorBold}❌ Error: {}{ColorReset}{} {ColorBorder}│{ColorReset}",
                    margin,
                    " ".repeat(left_pad),
                    err_trimmed,
                    " ".repeat(right_pad),
                    ColorBorder = COLOR_BORDER,
                    ColorReset = COLOR_RESET,
                    ColorError = COLOR_ERROR,
                    ColorBold = COLOR_BOLD
                );
            }
            RecordingState::Idle => {
                println!(
                    "{}{ColorBorder}│{ColorReset}           {ColorSuccess}{ColorBold}Press [Space] to record{ColorReset}            {ColorBorder}│{ColorReset}",
                    margin,
                    ColorBorder = COLOR_BORDER,
                    ColorReset = COLOR_RESET,
                    ColorSuccess = COLOR_SUCCESS,
                    ColorBold = COLOR_BOLD
                );
            }
        }

        // Content Line 2 (Dynamic Visual Area: Waveform, Spinner helper, Text preview, error help)
        match self.recording_state {
            RecordingState::Recording => {
                let wave_idx = self.animation_tick % WAVE_FRAMES.len();
                let wave = WAVE_FRAMES[wave_idx];
                println!(
                    "{}{ColorBorder}│{ColorReset}               {ColorRecord}{}{ColorReset}                {ColorBorder}│{ColorReset}",
                    margin,
                    wave,
                    ColorBorder = COLOR_BORDER,
                    ColorReset = COLOR_RESET,
                    ColorRecord = COLOR_RECORDING
                );
            }
            RecordingState::Transcribing => {
                // If the watchdog fired, surface its notice (otherwise default).
                let line = if !self.error_message.is_empty() {
                    self.error_message.clone()
                } else {
                    "AI is cleaning text...".to_string()
                };
                let len = line.chars().count();
                let left_pad = (44 - len) / 2;
                let right_pad = 44 - len - left_pad;
                println!(
                    "{}{ColorBorder}│{ColorReset}{}{ColorMuted}{}{ColorReset}{} {ColorBorder}│{ColorReset}",
                    margin,
                    " ".repeat(left_pad),
                    line,
                    " ".repeat(right_pad),
                    ColorBorder = COLOR_BORDER,
                    ColorReset = COLOR_RESET,
                    ColorMuted = COLOR_MUTED
                );
            }
            RecordingState::Done | RecordingState::Confirming => {
                let text_preview = text_preview(&self.transcription_text);
                let len = text_preview.chars().count();
                let left_pad = (44 - len) / 2;
                let right_pad = 44 - len - left_pad;
                println!(
                    "{}{ColorBorder}│{ColorReset}{}{ColorMuted}{}{ColorReset}{} {ColorBorder}│{ColorReset}",
                    margin,
                    " ".repeat(left_pad),
                    text_preview,
                    " ".repeat(right_pad),
                    ColorBorder = COLOR_BORDER,
                    ColorReset = COLOR_RESET,
                    ColorMuted = COLOR_MUTED
                );
            }
            RecordingState::Error => {
                println!(
                    "{}{ColorBorder}│{ColorReset}         {ColorMuted}Press [Space] to try again{ColorReset}          {ColorBorder}│{ColorReset}",
                    margin,
                    ColorBorder = COLOR_BORDER,
                    ColorReset = COLOR_RESET,
                    ColorMuted = COLOR_MUTED
                );
            }
            RecordingState::Idle => {
                let model_info = format!("Model: {}", self.config.model);
                let len = model_info.chars().count();
                let left_pad = (44 - len) / 2;
                let right_pad = 44 - len - left_pad;
                println!(
                    "{}{ColorBorder}│{ColorReset}{}{ColorMuted}{}{ColorReset}{} {ColorBorder}│{ColorReset}",
                    margin,
                    " ".repeat(left_pad),
                    model_info,
                    " ".repeat(right_pad),
                    ColorBorder = COLOR_BORDER,
                    ColorReset = COLOR_RESET,
                    ColorMuted = COLOR_MUTED
                );
            }
        }

        // Content Line 3 (Bottom helper / Cancel info)
        match self.recording_state {
            RecordingState::Recording => {
                println!(
                    "{}{ColorBorder}│{ColorReset}        {ColorMuted}Press [Space] again to Stop{ColorReset}        {ColorBorder}│{ColorReset}",
                    margin,
                    ColorBorder = COLOR_BORDER,
                    ColorReset = COLOR_RESET,
                    ColorMuted = COLOR_MUTED
                );
            }
            RecordingState::Confirming => {
                // Spec injection-confirmation: Enter injects, Esc cancels.
                println!(
                    "{}{ColorBorder}│{ColorReset}      {ColorMuted}[Enter] inject   [Esc] cancel{ColorReset}      {ColorBorder}│{ColorReset}",
                    margin,
                    ColorBorder = COLOR_BORDER,
                    ColorReset = COLOR_RESET,
                    ColorMuted = COLOR_MUTED
                );
            }
            RecordingState::Idle | RecordingState::Error => {
                println!(
                    "{}{ColorBorder}│{ColorReset}           {ColorMuted}Press [Esc] to cancel{ColorReset}            {ColorBorder}│{ColorReset}",
                    margin,
                    ColorBorder = COLOR_BORDER,
                    ColorReset = COLOR_RESET,
                    ColorMuted = COLOR_MUTED
                );
            }
            _ => {
                println!("{}{ColorBorder}│                                          │{ColorReset}", margin, ColorBorder = COLOR_BORDER, ColorReset = COLOR_RESET);
            }
        }

        // Bottom Border
        println!("{}{ColorBorder}└──────────────────────────────────────────┘{ColorReset}", margin, ColorBorder = COLOR_BORDER, ColorReset = COLOR_RESET);

        // Print remaining vertical padding
        for _ in 0..pad_y {
            println!();
        }
    }
}

impl State {
    fn start_recording(&mut self) {
        self.recording_state = RecordingState::Recording;
        self.error_message.clear();
        self.transcription_text.clear();
        self.seconds_elapsed = 0.0;
        self.animation_tick = 0;

        // Check if API key is empty
        if self.config.api_key.is_empty() {
            // Try to get it from environment
            if let Ok(key) = std::env::var("OPENROUTER_API_KEY") {
                self.config.api_key = key;
            } else {
                self.error_message = "Missing OPENROUTER_API_KEY config".to_string();
                self.recording_state = RecordingState::Error;
                return;
            }
        }

        // Setup context for the command callback
        let mut context = BTreeMap::new();
        context.insert("action".to_string(), "transcribe".to_string());

        // Start timer
        self.timer_active = true;
        set_timeout(0.1);

        let lock_file = lock_file_path(self.plugin_id);
        let audio_path = audio_file_path(self.plugin_id);

        // Spec secret-protection: the API key travels in the env-variable map
        // (never argv). argv holds only ["python3", script, lock_file] so `ps`
        // output never contains the key. The sidecar writes a 0600 key file and
        // scrubs its own environment.
        let env = self.config.build_sidecar_env(&audio_path, &lock_file);

        run_command_with_env_variables_and_cwd(
            &["python3", &self.config.script_path, &lock_file],
            env,
            std::path::PathBuf::from("."),
            context,
        );
    }

    fn stop_recording(&mut self) {
        self.recording_state = RecordingState::Transcribing;
        let lock_file = lock_file_path(self.plugin_id);

        // Delete lock file to signal python script to stop recording
        run_command(&["rm", "-f", &lock_file], BTreeMap::new());
    }

    /// Inject the transcribed text into the focused pane and close the plugin.
    ///
    /// Used by both the legacy auto-paste (`Done`) and the confirmation gate
    /// (`Confirming` + Enter). The text is sanitized again here as
    /// defense-in-depth (the sidecar already sanitized it). The temp text file
    /// written by the sidecar is removed after injection.
    fn inject_text_and_close(&mut self) {
        self.timer_active = false;
        let cleaned = sanitize::sanitize_terminal_text(&self.transcription_text);
        run_command(
            &["zellij", "action", "write-chars", &cleaned],
            BTreeMap::new(),
        );
        let text_file = text_file_path(self.plugin_id);
        run_command(&["rm", "-f", &text_file], BTreeMap::new());
        close_self();
    }
}

// --- Pure helpers (unit-tested, no zellij-tile dependency) ----------------

/// Prefix shared by every temp file the plugin + sidecar exchange.
const TEMP_PREFIX: &str = "/tmp/zellij-voice-";

fn lock_file_path(plugin_id: u32) -> String {
    format!("{}{}.recording", TEMP_PREFIX, plugin_id)
}

fn text_file_path(plugin_id: u32) -> String {
    format!("{}{}.txt", TEMP_PREFIX, plugin_id)
}

fn audio_file_path(plugin_id: u32) -> String {
    format!("{}{}.wav", TEMP_PREFIX, plugin_id)
}

/// Decide which state to enter after a successful transcription.
///
/// Spec `injection-confirmation`: with `confirm_inject` (default true) the
/// plugin MUST show a preview and wait for Enter before pasting, so it enters
/// [`RecordingState::Confirming`]. Disabling it restores the legacy instant
/// paste via [`RecordingState::Done`].
fn next_state_after_transcription(confirm_inject: bool) -> RecordingState {
    if confirm_inject {
        RecordingState::Confirming
    } else {
        RecordingState::Done
    }
}

/// Watchdog predicate: has the recording exceeded the configured max duration?
///
/// Spec `recording-lifecycle`: recording longer than the cap MUST auto-stop.
/// Fires at the boundary (>=) so exactly `max_duration` seconds triggers it.
fn recording_exceeded_max_duration(seconds_elapsed: f32, max_duration: u32) -> bool {
    seconds_elapsed >= max_duration as f32
}

/// Build a short, quoted preview of `text` for the Done/Confirming UI.
///
/// Keeps the rendered line within the fixed-width box: full text quoted when it
/// fits (<= 30 chars), else the first 27 chars + `"..."`. Operates on `char`s
/// so multi-byte UTF-8 (accents/emoji) is never split.
fn text_preview(text: &str) -> String {
    const PREVIEW_MAX: usize = 30;
    const PREVIEW_KEEP: usize = 27;
    if text.chars().count() > PREVIEW_MAX {
        format!("\"{}...\"", text.chars().take(PREVIEW_KEEP).collect::<String>())
    } else {
        format!("\"{}\"", text)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // --- temp path helpers (lock/text/audio share a prefix + plugin id) ---

    #[test]
    fn lock_file_path_uses_plugin_id_and_recording_suffix() {
        assert_eq!(lock_file_path(42), "/tmp/zellij-voice-42.recording");
    }

    #[test]
    fn text_file_path_uses_plugin_id_and_txt_suffix() {
        assert_eq!(text_file_path(7), "/tmp/zellij-voice-7.txt");
    }

    #[test]
    fn audio_file_path_uses_plugin_id_and_wav_suffix() {
        assert_eq!(audio_file_path(99), "/tmp/zellij-voice-99.wav");
    }

    // --- post-transcription state (spec injection-confirmation gate) ---

    #[test]
    fn next_state_confirming_when_confirm_inject_true() {
        assert_eq!(next_state_after_transcription(true), RecordingState::Confirming);
    }

    #[test]
    fn next_state_done_when_confirm_inject_false() {
        // Legacy instant-paste path.
        assert_eq!(next_state_after_transcription(false), RecordingState::Done);
    }

    // --- max-duration watchdog (spec recording-lifecycle) ---

    #[test]
    fn watchdog_false_below_cap() {
        assert!(!recording_exceeded_max_duration(119.9, 120));
        assert!(!recording_exceeded_max_duration(0.0, 120));
    }

    #[test]
    fn watchdog_true_at_and_above_cap() {
        // Boundary: exactly max_duration triggers the auto-stop.
        assert!(recording_exceeded_max_duration(120.0, 120));
        assert!(recording_exceeded_max_duration(150.0, 120));
    }

    #[test]
    fn watchdog_respects_configured_cap() {
        // A non-default cap (e.g. 60s) is honored.
        assert!(!recording_exceeded_max_duration(59.0, 60));
        assert!(recording_exceeded_max_duration(60.0, 60));
    }

    // --- text preview (Done/Confirming UI helper) ---

    #[test]
    fn text_preview_short_text_is_quoted_full() {
        assert_eq!(text_preview("hi"), "\"hi\"");
        assert_eq!(text_preview(""), "\"\"");
    }

    #[test]
    fn text_preview_long_text_is_truncated_with_ellipsis() {
        let long: String = "x".repeat(40);
        let p = text_preview(&long);
        assert!(p.starts_with("\""));
        assert!(p.ends_with("...\""));
        // First 27 chars kept between the quote and the ellipsis.
        assert_eq!(p, format!("\"{}...\"", "x".repeat(27)));
    }

    #[test]
    fn text_preview_utf8_not_split_mid_codepoint() {
        // Accents/emoji counted as chars, never split a multi-byte sequence.
        let p = text_preview("áéíóú") ; // 5 chars, short -> full
        assert_eq!(p, "\"áéíóú\"");
    }
}
