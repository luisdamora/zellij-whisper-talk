use std::collections::BTreeMap;
use zellij_tile::prelude::*;

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
    // Configuration properties
    api_key: String,
    model: String,
    script_path: String,
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

        // Read configuration
        self.api_key = configuration
            .get("api_key")
            .cloned()
            .unwrap_or_default();
        
        self.model = configuration
            .get("model")
            .cloned()
            .unwrap_or_else(|| "deepseek/deepseek-v4-flash".to_string());
        
        self.script_path = configuration
            .get("script_path")
            .cloned()
            .unwrap_or_else(|| "/mnt/E608E9D408E9A431/Caprinosol/zellij-voice-input/scripts/transcribe.py".to_string());

        self.plugin_id = get_plugin_ids().plugin_id;
        self.initialized = true;
        eprintln!("Zellij Whisper Talk: load() complete. Initialized: {}, script_path: {}, plugin_id: {}", self.initialized, self.script_path, self.plugin_id);
    }

    fn update(&mut self, event: Event) -> bool {
        eprintln!("Zellij Whisper Talk: update() called with event: {:?}", event);
        let mut should_render = false;

        match event {
            Event::Key(key) => {
                match key.bare_key {
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
                        if self.recording_state == RecordingState::Recording {
                            let lock_file = format!("/tmp/zellij-voice-{}.recording", self.plugin_id);
                            run_command(&["rm", "-f", &lock_file], BTreeMap::new());
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
                        RecordingState::Recording | RecordingState::Transcribing => {
                            set_timeout(0.1);
                        }
                        RecordingState::Done => {
                            if self.seconds_elapsed >= 1.2 {
                                self.timer_active = false;
                                let text_file = format!("/tmp/zellij-voice-{}.txt", self.plugin_id);
                                let cmd = format!("python3 -c \"import subprocess, time, os; time.sleep(0.15); text = open('{}').read(); subprocess.run(['zellij', 'action', 'write-chars', text]); os.remove('{}')\"", text_file, text_file);
                                run_command(&["sh", "-c", &cmd], BTreeMap::new());
                                close_self();
                            } else {
                                set_timeout(0.1);
                            }
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
                            self.transcription_text = text;
                            self.recording_state = RecordingState::Done;
                            self.seconds_elapsed = 0.0;
                            // Keep timer active to display Done screen for a bit
                            self.timer_active = true;
                            set_timeout(0.1);
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
                println!(
                    "{}{ColorBorder}│{ColorReset}          {ColorMuted}AI is cleaning text...{ColorReset}           {ColorBorder}│{ColorReset}",
                    margin,
                    ColorBorder = COLOR_BORDER,
                    ColorReset = COLOR_RESET,
                    ColorMuted = COLOR_MUTED
                );
            }
            RecordingState::Done => {
                let text_preview = if self.transcription_text.chars().count() > 30 {
                    format!("\"{}...\"", self.transcription_text.chars().take(27).collect::<String>())
                } else {
                    format!("\"{}\"", self.transcription_text)
                };
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
                let model_info = format!("Model: {}", self.model);
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
        if self.api_key.is_empty() {
            // Try to get it from environment
            if let Ok(key) = std::env::var("OPENROUTER_API_KEY") {
                self.api_key = key;
            } else {
                self.error_message = "Missing OPENROUTER_API_KEY config".to_string();
                self.recording_state = RecordingState::Error;
                return;
            }
        }

        // Setup context for the command callback
        let mut context = BTreeMap::new();
        context.insert("action".to_string(), "transcribe".to_string());

        // Prepare environment variables using the env helper command
        let api_key_env = format!("OPENROUTER_API_KEY={}", self.api_key);
        let model_env = format!("OPENROUTER_MODEL={}", self.model);

        // Start timer
        self.timer_active = true;
        set_timeout(0.1);

        let lock_file = format!("/tmp/zellij-voice-{}.recording", self.plugin_id);
        let audio_env = format!("AUDIO_PATH=/tmp/zellij-voice-{}.wav", self.plugin_id);

        // Run the script: env OPENROUTER_API_KEY=xxx OPENROUTER_MODEL=yyy AUDIO_PATH=... python3 transcribe.py /tmp/zellij-voice-ID.recording
        run_command(
            &[
                "env",
                &api_key_env,
                &model_env,
                &audio_env,
                "python3",
                &self.script_path,
                &lock_file,
            ],
            context,
        );
    }

    fn stop_recording(&mut self) {
        self.recording_state = RecordingState::Transcribing;
        let lock_file = format!("/tmp/zellij-voice-{}.recording", self.plugin_id);

        // Delete lock file to signal python script to stop recording
        run_command(&["rm", "-f", &lock_file], BTreeMap::new());
    }
}
