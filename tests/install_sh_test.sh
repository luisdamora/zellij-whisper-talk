#!/usr/bin/env bash
# Tests for scripts/install.sh — zero dependencies, stubs network with a fake curl.
#
# Run: bash tests/install_sh_test.sh
#
# Covers the two regressions fixed in v0.1.1:
#   1. `curl | bash` stdin contention: piping the script must not corrupt config.
#   2. Append path must wrap `shared_except` inside a `keybinds {}` block.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_SH="${SCRIPT_DIR}/scripts/install.sh"

PASS=0
FAIL=0
FAILED_NAMES=()

pass() { PASS=$((PASS + 1)); echo "  ok   - $1"; }
fail() { FAIL=$((FAIL + 1)); FAILED_NAMES+=("$1"); echo "  FAIL - $1"; [[ -n "${2:-}" ]] && echo "         $2"; }

# Assert that a KDL file has balanced braces (depth returns to 0).
brace_depth() {
    awk '{for(i=1;i<=length($0);i++){c=substr($0,i,1);if(c=="{")d++;if(c=="}")d--}} END{print d+0}' "$1"
}

# Build an isolated sandbox: a fake `curl` on PATH that just creates the -o target,
# so the real install.sh never touches the network.
make_sandbox() {
    local box; box="$(mktemp -d)"
    mkdir -p "${box}/bin"
    cat > "${box}/bin/curl" <<'STUB'
#!/usr/bin/env bash
# Fake curl: honor `-o <file>` by creating a dummy file; ignore everything else.
out=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        *) shift ;;
    esac
done
[[ -n "$out" ]] && { mkdir -p "$(dirname "$out")"; printf 'stub' > "$out"; }
exit 0
STUB
    chmod +x "${box}/bin/curl"
    echo "$box"
}

run_install() {
    # run_install <sandbox> <stdin-source> [args...]
    local box="$1"; local stdin_src="$2"; shift 2
    PATH="${box}/bin:${PATH}" bash "$INSTALL_SH" \
        --plugins-dir "${box}/plugins" \
        --config "${box}/config.kdl" \
        "$@" < "$stdin_src" > "${box}/stdout.log" 2>&1
}

# ── Test 1: fresh config is created wrapped in keybinds {} with the real key ──
test_fresh_config_is_valid_kdl() {
    local box; box="$(make_sandbox)"
    run_install "$box" /dev/null --non-interactive --api-key sk-test --keybind "Ctrl y"
    local cfg="${box}/config.kdl"

    [[ -f "$cfg" ]] || { fail "fresh config: file created" "no config written"; return; }
    grep -q 'keybinds {' "$cfg" || { fail "fresh config: wrapped in keybinds" ; return; }
    grep -q 'bind "Ctrl y"' "$cfg" || { fail "fresh config: real keybind present"; return; }
    [[ "$(brace_depth "$cfg")" == "0" ]] || { fail "fresh config: balanced braces"; return; }
    pass "fresh config is valid KDL wrapped in keybinds with the real key"
}

# ── Test 2: append path wraps shared_except inside keybinds (regression #2) ──
test_append_wraps_in_keybinds() {
    local box; box="$(make_sandbox)"
    # Pre-existing config with its OWN closed keybinds block and no plugin yet.
    cat > "${box}/config.kdl" <<'KDL'
keybinds clear-defaults=true {
    normal {
        bind "Ctrl g" { SwitchToMode "locked"; }
    }
}
KDL
    run_install "$box" /dev/null --non-interactive --api-key sk-test --keybind "Ctrl y"
    local cfg="${box}/config.kdl"

    grep -q 'shared_except "locked"' "$cfg" || { fail "append: shared_except injected"; return; }
    # The appended block must introduce its own keybinds wrapper, never a bare shared_except.
    [[ "$(grep -c 'keybinds' "$cfg")" -ge 2 ]] || { fail "append: appended block has its own keybinds wrapper"; return; }
    [[ "$(brace_depth "$cfg")" == "0" ]] || { fail "append: whole file stays balanced"; return; }
    pass "append path wraps shared_except inside keybinds and stays balanced"
}

# ── Test 3: the bind key is the provided value, never a comment line ──
test_keybind_is_not_a_comment() {
    local box; box="$(make_sandbox)"
    run_install "$box" /dev/null --non-interactive --api-key sk-test --keybind "Alt y"
    local cfg="${box}/config.kdl"

    grep -q 'bind "Alt y"' "$cfg" || { fail "keybind: substitutes the provided key"; return; }
    grep -q 'bind "#' "$cfg" && { fail "keybind: a comment leaked in as the bind key"; return; }
    pass "bind key is the provided value, never a comment line"
}

# ── Test 4: `curl | bash` invocation form — script piped on stdin (regression #1) ──
# In non-interactive mode no prompts run, so piping the script must yield a correct
# config rather than consuming its own lines as input.
test_curl_pipe_form_non_interactive() {
    local box; box="$(make_sandbox)"
    PATH="${box}/bin:${PATH}" bash -s -- \
        --non-interactive --api-key sk-test --keybind "Ctrl y" \
        --plugins-dir "${box}/plugins" --config "${box}/config.kdl" \
        < "$INSTALL_SH" > "${box}/stdout.log" 2>&1
    local cfg="${box}/config.kdl"

    [[ -f "$cfg" ]] || { fail "curl|bash form: config created"; return; }
    grep -q 'bind "Ctrl y"' "$cfg" || { fail "curl|bash form: key not corrupted by stdin"; return; }
    pass "curl|bash (piped stdin) non-interactive form produces a clean config"
}

# ── Test 5: already-configured config is left untouched (no duplicate inject) ──
test_already_configured_is_skipped() {
    local box; box="$(make_sandbox)"
    cat > "${box}/config.kdl" <<'KDL'
keybinds {
    shared_except "locked" {
        bind "Ctrl y" {
            LaunchOrFocusPlugin "file:/x/zellij_whisper_talk.wasm" { floating true; }
        }
    }
}
KDL
    run_install "$box" /dev/null --non-interactive --api-key sk-test
    local cfg="${box}/config.kdl"

    [[ "$(grep -c 'zellij_whisper_talk.wasm' "$cfg")" == "1" ]] || { fail "already-configured: not duplicated"; return; }
    grep -q 'skipping config injection' "${box}/stdout.log" || { fail "already-configured: warns about skip"; return; }
    pass "already-configured config is detected and not duplicated"
}

echo "Running install.sh tests..."
test_fresh_config_is_valid_kdl
test_append_wraps_in_keybinds
test_keybind_is_not_a_comment
test_curl_pipe_form_non_interactive
test_already_configured_is_skipped

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"
if [[ "$FAIL" -gt 0 ]]; then
    printf '  - %s\n' "${FAILED_NAMES[@]}"
    exit 1
fi
