//! Pure configuration parser for the Zellij plugin.
//!
//! Reads the `BTreeMap<String, String>` handed to `ZellijPlugin::load` and
//! produces a typed `PluginConfig`. All new keys are optional with safe
//! defaults, so existing `plugin.kdl` configurations keep working (backward
//! compatible). No zellij-tile dependency — unit-tested on host target.
//!
//! Spec: secret-protection (api_key handling), recording-lifecycle
//! (max_duration default/cap).

use std::collections::BTreeMap;

/// Default cleanup/chat model (status quo from `main.rs::load`).
const DEFAULT_MODEL: &str = "deepseek/deepseek-v4-flash";

/// Default script path (status quo absolute path from `main.rs::load`).
/// Kept identical so existing `plugin.kdl` configs keep working.
const DEFAULT_SCRIPT_PATH: &str =
    "/mnt/E608E9D408E9A431/Caprinosol/zellij-voice-input/scripts/transcribe.py";

const DEFAULT_MAX_DURATION: u32 = 120;
const DEFAULT_HTTP_TIMEOUT: u32 = 30;
const DEFAULT_HTTP_RETRIES: u32 = 3;
const DEFAULT_AUDIO_BACKEND: &str = "auto";

/// Typed, resolved configuration for the whisper-talk plugin.
///
/// Every field has a safe default, so all keys in `plugin.kdl` are optional
/// (backward compatible). Built purely from a `BTreeMap<String, String>`,
/// independent of zellij-tile, which makes it trivially unit-testable.
pub struct PluginConfig {
    pub api_key: String,
    pub model: String,
    pub script_path: String,
    pub max_duration: u32,
    pub audio_backend: String,
    pub confirm_inject: bool,
    pub http_timeout: u32,
    pub http_retries: u32,
}

impl Default for PluginConfig {
    /// Defaults mirror `from_btreemap(&empty)` so `State` can derive `Default`
    /// and start from a sane, unconfigured state until `load()` runs.
    fn default() -> Self {
        Self {
            api_key: String::new(),
            model: DEFAULT_MODEL.to_string(),
            script_path: DEFAULT_SCRIPT_PATH.to_string(),
            max_duration: DEFAULT_MAX_DURATION,
            audio_backend: DEFAULT_AUDIO_BACKEND.to_string(),
            confirm_inject: true,
            http_timeout: DEFAULT_HTTP_TIMEOUT,
            http_retries: DEFAULT_HTTP_RETRIES,
        }
    }
}

impl PluginConfig {
    /// Build a `PluginConfig` from the raw configuration map handed to
    /// `ZellijPlugin::load`. Missing keys use safe defaults; malformed numeric
    /// or boolean values fall back to their defaults rather than panicking.
    pub fn from_btreemap(config: &BTreeMap<String, String>) -> Self {
        Self {
            api_key: config.get("api_key").cloned().unwrap_or_default(),
            model: config
                .get("model")
                .cloned()
                .unwrap_or_else(|| DEFAULT_MODEL.to_string()),
            script_path: config
                .get("script_path")
                .cloned()
                .unwrap_or_else(|| DEFAULT_SCRIPT_PATH.to_string()),
            max_duration: parse_u32(config, "max_duration", DEFAULT_MAX_DURATION),
            audio_backend: config
                .get("audio_backend")
                .cloned()
                .unwrap_or_else(|| DEFAULT_AUDIO_BACKEND.to_string()),
            confirm_inject: parse_bool(config, "confirm_inject", true),
            http_timeout: parse_u32(config, "http_timeout", DEFAULT_HTTP_TIMEOUT),
            http_retries: parse_u32(config, "http_retries", DEFAULT_HTTP_RETRIES),
        }
    }

    /// Build the env-variable map handed to the Python sidecar via
    /// `run_command_with_env_variables_and_cwd`.
    ///
    /// Spec `secret-protection`: the API key travels in this env map (never in
    /// argv), so `ps` output never contains it. The sidecar writes a 0600 key
    /// file from this env value and then scrubs its own environment. All
    /// sidecar config also flows through here so a single channel carries every
    /// tunable. Pure function of the config + the two runtime paths.
    pub fn build_sidecar_env(
        &self,
        audio_path: &str,
        lock_file: &str,
    ) -> BTreeMap<String, String> {
        let mut env = BTreeMap::new();
        env.insert("OPENROUTER_API_KEY".to_string(), self.api_key.clone());
        env.insert("OPENROUTER_MODEL".to_string(), self.model.clone());
        env.insert("AUDIO_PATH".to_string(), audio_path.to_string());
        env.insert("MAX_DURATION".to_string(), self.max_duration.to_string());
        env.insert("AUDIO_BACKEND".to_string(), self.audio_backend.clone());
        env.insert("HTTP_TIMEOUT".to_string(), self.http_timeout.to_string());
        env.insert("HTTP_RETRIES".to_string(), self.http_retries.to_string());
        env.insert("LOCK_FILE".to_string(), lock_file.to_string());
        env
    }
}

/// Parse a `u32` config value by key, returning `default` on absence or parse
/// failure. Guarantees a valid, bounded number is always produced.
fn parse_u32(config: &BTreeMap<String, String>, key: &str, default: u32) -> u32 {
    config
        .get(key)
        .and_then(|v| v.trim().parse::<u32>().ok())
        .unwrap_or(default)
}

/// Parse a boolean config value by key. Recognises `"true"`/`"false"`
/// (case-insensitive); anything else (including absence) returns `default`.
fn parse_bool(config: &BTreeMap<String, String>, key: &str, default: bool) -> bool {
    match config.get(key).map(|v| v.trim().to_ascii_lowercase()).as_deref() {
        Some("true") => true,
        Some("false") => false,
        _ => default,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    fn empty() -> BTreeMap<String, String> {
        BTreeMap::new()
    }

    // Scenario: empty configuration -> every field takes its safe default.
    #[test]
    fn defaults_when_empty() {
        let cfg = PluginConfig::from_btreemap(&empty());
        assert_eq!(cfg.api_key, "");
        assert_eq!(cfg.model, "deepseek/deepseek-v4-flash");
        assert_eq!(cfg.audio_backend, "auto");
        assert_eq!(cfg.confirm_inject, true);
        assert_eq!(cfg.max_duration, 120);
        assert_eq!(cfg.http_timeout, 30);
        assert_eq!(cfg.http_retries, 3);
        // script_path default is non-empty (status quo absolute path).
        assert!(!cfg.script_path.is_empty());
    }

    // Scenario: all keys overridden -> typed values parsed from the map.
    #[test]
    fn overrides_all_fields() {
        let mut m = empty();
        m.insert("api_key".into(), "sk-test-123".into());
        m.insert("model".into(), "openai/gpt-4o".into());
        m.insert("script_path".into(), "/opt/app/transcribe.py".into());
        m.insert("max_duration".into(), "90".into());
        m.insert("audio_backend".into(), "arecord".into());
        m.insert("confirm_inject".into(), "false".into());
        m.insert("http_timeout".into(), "45".into());
        m.insert("http_retries".into(), "7".into());

        let cfg = PluginConfig::from_btreemap(&m);
        assert_eq!(cfg.api_key, "sk-test-123");
        assert_eq!(cfg.model, "openai/gpt-4o");
        assert_eq!(cfg.script_path, "/opt/app/transcribe.py");
        assert_eq!(cfg.max_duration, 90);
        assert_eq!(cfg.audio_backend, "arecord");
        assert_eq!(cfg.confirm_inject, false);
        assert_eq!(cfg.http_timeout, 45);
        assert_eq!(cfg.http_retries, 7);
    }

    // Triangulation: max_duration boundary — invalid value falls back to default.
    #[test]
    fn max_duration_invalid_falls_back() {
        let mut m = empty();
        m.insert("max_duration".into(), "not-a-number".into());
        let cfg = PluginConfig::from_btreemap(&m);
        assert_eq!(cfg.max_duration, 120);
    }

    // Triangulation: confirm_inject bool parsing.
    #[test]
    fn confirm_inject_bool_parsing() {
        let mut m = empty();
        m.insert("confirm_inject".into(), "false".into());
        assert_eq!(PluginConfig::from_btreemap(&m).confirm_inject, false);

        let mut m2 = empty();
        m2.insert("confirm_inject".into(), "true".into());
        assert_eq!(PluginConfig::from_btreemap(&m2).confirm_inject, true);

        // Invalid -> safe default (true).
        let mut m3 = empty();
        m3.insert("confirm_inject".into(), "maybe".into());
        assert_eq!(PluginConfig::from_btreemap(&m3).confirm_inject, true);
    }

    // Triangulation: http_timeout / http_retries invalid -> defaults.
    #[test]
    fn http_numeric_fields_invalid_fall_back() {
        let mut m = empty();
        m.insert("http_timeout".into(), "oops".into());
        m.insert("http_retries".into(), "".into());
        let cfg = PluginConfig::from_btreemap(&m);
        assert_eq!(cfg.http_timeout, 30);
        assert_eq!(cfg.http_retries, 3);
    }

    // Scenario: build_sidecar_env carries the API key + every tunable, and the
    // key is the canonical OPENROUTER_API_KEY name (secret-protection: key
    // travels via env map, never argv).
    #[test]
    fn build_sidecar_env_carries_key_and_all_tunables() {
        let mut m = empty();
        m.insert("api_key".into(), "sk-hidden".into());
        m.insert("model".into(), "openai/gpt-4o".into());
        m.insert("audio_backend".into(), "pw-record".into());
        m.insert("http_timeout".into(), "45".into());
        m.insert("http_retries".into(), "7".into());
        m.insert("max_duration".into(), "90".into());
        let cfg = PluginConfig::from_btreemap(&m);

        let env = cfg.build_sidecar_env("/tmp/a.wav", "/tmp/lock");
        assert_eq!(env.get("OPENROUTER_API_KEY"), Some(&"sk-hidden".to_string()));
        assert_eq!(env.get("OPENROUTER_MODEL"), Some(&"openai/gpt-4o".to_string()));
        assert_eq!(env.get("AUDIO_PATH"), Some(&"/tmp/a.wav".to_string()));
        assert_eq!(env.get("MAX_DURATION"), Some(&"90".to_string()));
        assert_eq!(env.get("AUDIO_BACKEND"), Some(&"pw-record".to_string()));
        assert_eq!(env.get("HTTP_TIMEOUT"), Some(&"45".to_string()));
        assert_eq!(env.get("HTTP_RETRIES"), Some(&"7".to_string()));
        assert_eq!(env.get("LOCK_FILE"), Some(&"/tmp/lock".to_string()));
    }

    // Triangulation: defaults flow through when config is empty.
    #[test]
    fn build_sidecar_env_uses_defaults_when_unconfigured() {
        let cfg = PluginConfig::from_btreemap(&empty());
        let env = cfg.build_sidecar_env("/tmp/a.wav", "/tmp/lock");
        assert_eq!(env.get("HTTP_TIMEOUT"), Some(&"30".to_string()));
        assert_eq!(env.get("HTTP_RETRIES"), Some(&"3".to_string()));
        assert_eq!(env.get("MAX_DURATION"), Some(&"120".to_string()));
        assert_eq!(env.get("AUDIO_BACKEND"), Some(&"auto".to_string()));
        // Empty key still present (sidecar decides what to do).
        assert_eq!(env.get("OPENROUTER_API_KEY"), Some(&"".to_string()));
    }
}
