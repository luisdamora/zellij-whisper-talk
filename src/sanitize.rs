//! Output sanitization for terminal injection (defense-in-depth, Rust side).
//!
//! Strips ANSI CSI/OSC escape runs and C0 control bytes from text before it
//! reaches `zellij action write-chars`. Newline (`\n`) and tab (`\t`) are
//! preserved. Pure, no zellij-tile dependency — unit-tested on host target.
//!
//! Spec: output-sanitization (scenarios: ANSI color codes, control bytes).

/// Strip ANSI escape sequences and C0 control bytes from terminal-bound text.
///
/// Removes:
/// - CSI runs: `ESC [` + params/intermediates + final byte (`@`-`~`)
/// - OSC runs: `ESC ]` ... `BEL` or string-terminator (`ESC \`)
/// - Other `ESC <byte>` two-byte escapes
/// - C0 control bytes (`0x00`-`0x1f`) **except** `\n` (`0x0a`) and `\t` (`0x09`)
///
/// Bytes `>= 0x80` are always preserved: the input is valid UTF-8, so those
/// bytes are continuation/lead bytes of multi-byte characters (the cleanup
/// prompt is Spanish, so accents/emoji must survive). 8-bit C1 CSI/OSC
/// introducers (`0x9b`/`0x9d`) cannot occur in a valid `&str`, so C1 is
/// inherently absent and stripping it would only corrupt text.
pub fn sanitize_terminal_text(input: &str) -> String {
    #[derive(Clone, Copy, PartialEq)]
    enum State {
        Normal,
        Esc,            // saw 0x1b
        Csi,            // inside ESC [ ... (until final 0x40-0x7e)
        Osc,            // inside ESC ] ... (until BEL or ST)
        OscEsc,         // inside OSC, saw ESC (expecting '\' of ST)
        EscIntermediate, // ESC + 0x20-0x2f (collecting until final 0x30-0x7e)
    }

    let bytes = input.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(bytes.len());
    let mut state = State::Normal;

    for &b in bytes {
        state = match state {
            State::Normal => {
                match b {
                    0x1b => State::Esc,
                    // C0 control: drop everything except \n (0x0a) and \t (0x09).
                    c if c < 0x20 && c != 0x0a && c != 0x09 => State::Normal,
                    _ => {
                        out.push(b);
                        State::Normal
                    }
                }
            }
            State::Esc => match b {
                b'[' => State::Csi,
                b']' => State::Osc,
                0x20..=0x2f => State::EscIntermediate,
                // Any other byte: a two-byte escape (e.g. ESC M). Drop it, done.
                _ => State::Normal,
            },
            State::Csi => {
                match b {
                    0x40..=0x7e => State::Normal, // final byte ends the CSI run
                    _ => State::Csi,             // params/intermediates continue
                }
            }
            State::Osc => match b {
                0x07 => State::Normal, // BEL ends OSC
                0x1b => State::OscEsc, // possible ST (ESC \)
                _ => State::Osc,
            },
            State::OscEsc => match b {
                b'\\' => State::Normal, // ST terminator
                _ => State::Osc,        // stray ESC inside OSC, keep consuming
            },
            State::EscIntermediate => match b {
                0x20..=0x2f => State::EscIntermediate,
                0x30..=0x7e => State::Normal, // final byte
                _ => State::Normal,
            },
        };
    }

    // SAFETY: we only dropped ASCII bytes (< 0x80); all multi-byte UTF-8
    // sequences (bytes >= 0x80) are intact, so `out` is valid UTF-8.
    String::from_utf8(out).expect("sanitized bytes remain valid UTF-8")
}

#[cfg(test)]
mod tests {
    use super::*;

    // Scenario: ANSI color codes present -> stripped, zero ESC bytes remain.
    #[test]
    fn strips_csi_color_codes() {
        let input = "\x1b[31mred\x1b[0m";
        let out = sanitize_terminal_text(input);
        assert_eq!(out, "red");
        assert!(
            !out.as_bytes().contains(&0x1b),
            "no ESC byte should remain, got: {:?}",
            out
        );
    }

    // Scenario: control bytes present -> all C0 except \n and \t removed.
    #[test]
    fn strips_c0_control_bytes() {
        // \x08 backspace, \x07 bell, \x0c form-feed must be removed.
        let input = "\x08back\x07bell\x0cform";
        let out = sanitize_terminal_text(input);
        assert_eq!(out, "backbellform");
    }

    // Triangulation: newline and tab MUST be preserved.
    #[test]
    fn preserves_newline_and_tab() {
        let input = "line1\n\tindented\x1b[0m";
        let out = sanitize_terminal_text(input);
        assert_eq!(out, "line1\n\tindented");
    }

    // Triangulation: OSC (Operating System Command) sequences stripped.
    #[test]
    fn strips_osc_sequences() {
        // OSC ended by BEL (\x07).
        let input = "\x1b]0;window-title\x07visible";
        let out = sanitize_terminal_text(input);
        assert_eq!(out, "visible");
    }

    // Triangulation: plain text and empty string pass through unchanged.
    #[test]
    fn plain_text_unchanged() {
        assert_eq!(sanitize_terminal_text("hello world"), "hello world");
        assert_eq!(sanitize_terminal_text(""), "");
    }

    // Triangulation: UTF-8 multi-byte characters (accents/emoji) preserved.
    #[test]
    fn preserves_utf8_multibyte() {
        let input = "café \u{1f44d} ñandú";
        assert_eq!(sanitize_terminal_text(input), "café \u{1f44d} ñandú");
    }
}
