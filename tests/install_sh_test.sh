#!/usr/bin/env bash
# Tests for scripts/install.sh — zero external deps, stubs the network with a
# fake curl that serves a valid WASM and a real sidecar tarball.
#
# Run: bash tests/install_sh_test.sh
#
# Regressions covered (all found by running the real install end-to-end):
#   - curl|bash stdin contention (prompts must read from /dev/tty, not fd 0)
#   - Zellij ignores a 2nd top-level keybinds block -> must insert into existing
#   - 404 saved as the .wasm ("Not Found") -> curl --fail + magic-byte check
#   - multi-file Python sidecar must be shipped + extracted (whisper_sidecar/)
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

# Build an isolated sandbox with a fake `curl` on PATH. The stub inspects the
# requested URL and serves realistic content: a valid WASM module (magic \0asm)
# and a real gzip tarball containing transcribe.py + whisper_sidecar/. Set
# STUB_MODE=bad_wasm in the environment to make the .wasm download return a
# "Not Found" body instead (simulating a 404 that curl -f would reject).
make_sandbox() {
    local box; box="$(mktemp -d)"
    mkdir -p "${box}/bin"
    cat > "${box}/bin/curl" <<'STUB'
#!/usr/bin/env bash
url=""; out=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        -*) shift ;;          # ignore flags like -fsSL
        *)  url="$1"; shift ;; # positional arg = URL
    esac
done
[[ -z "$out" ]] && exit 0
mkdir -p "$(dirname "$out")"
case "$url" in
    *.wasm)
        if [[ "${STUB_MODE:-}" == "bad_wasm" ]]; then
            printf 'Not Found' > "$out"
        else
            python3 -c "import sys;open(sys.argv[1],'wb').write(b'\x00asm\x01\x00\x00\x00')" "$out"
        fi ;;
    *.tar.gz)
        t="$(mktemp -d)"
        mkdir -p "$t/whisper_sidecar"
        printf '#!/usr/bin/env python3\n' > "$t/transcribe.py"
        printf '' > "$t/whisper_sidecar/__init__.py"
        printf 'X = 1\n' > "$t/whisper_sidecar/config.py"
        tar -czf "$out" -C "$t" transcribe.py whisper_sidecar
        rm -rf "$t" ;;
    *) printf 'stub' > "$out" ;;
esac
exit 0
STUB
    chmod +x "${box}/bin/curl"
    echo "$box"
}

run_install() {
    # run_install <sandbox> <stdin-source> [args...]; returns install.sh's exit code.
    local box="$1"; local stdin_src="$2"; shift 2
    PATH="${box}/bin:${PATH}" bash "$INSTALL_SH" \
        --plugins-dir "${box}/plugins" \
        --config "${box}/config.kdl" \
        "$@" < "$stdin_src" > "${box}/stdout.log" 2>&1
}

# ── Test 1: fresh config gets exactly one keybinds block with the real key ──
test_fresh_config_creates_single_keybinds_block() {
    local box; box="$(make_sandbox)"
    run_install "$box" /dev/null --non-interactive --api-key sk-test --keybind "Ctrl y"
    local cfg="${box}/config.kdl"

    [[ -f "$cfg" ]] || { fail "fresh: file created"; return; }
    [[ "$(grep -cE '^keybinds' "$cfg")" == "1" ]] || { fail "fresh: single keybinds block"; return; }
    grep -q 'bind "Ctrl y"' "$cfg" || { fail "fresh: real keybind present"; return; }
    [[ "$(brace_depth "$cfg")" == "0" ]] || { fail "fresh: balanced braces"; return; }
    pass "fresh config creates a single keybinds block with the real key"
}

# ── Test 2: insert into the EXISTING keybinds block, never a 2nd one ──
test_inserts_into_existing_keybinds_block() {
    local box; box="$(make_sandbox)"
    cat > "${box}/config.kdl" <<'KDL'
keybinds clear-defaults=true {
    normal {
        bind "Ctrl g" { SwitchToMode "locked"; }
    }
}
KDL
    run_install "$box" /dev/null --non-interactive --api-key sk-test --keybind "Ctrl y"
    local cfg="${box}/config.kdl"

    grep -q 'zellij_whisper_talk.wasm' "$cfg" || { fail "insert: plugin bind injected"; return; }
    # CRITICAL: Zellij processes only the first top-level keybinds block, so a
    # second one would be silently ignored. There must remain exactly ONE.
    [[ "$(grep -cE '^keybinds' "$cfg")" == "1" ]] || { fail "insert: must not create a 2nd keybinds block"; return; }
    grep -q 'bind "Ctrl g"' "$cfg" || { fail "insert: preserves the existing binds"; return; }
    [[ "$(brace_depth "$cfg")" == "0" ]] || { fail "insert: file stays balanced"; return; }
    pass "inserts the plugin bind into the existing keybinds block (no 2nd block)"
}

# ── Test 3: the bind key is the provided value, never a comment line ──
test_keybind_is_the_provided_key() {
    local box; box="$(make_sandbox)"
    run_install "$box" /dev/null --non-interactive --api-key sk-test --keybind "Alt y"
    local cfg="${box}/config.kdl"

    grep -q 'bind "Alt y"' "$cfg" || { fail "keybind: substitutes the provided key"; return; }
    grep -q 'bind "#' "$cfg" && { fail "keybind: a comment leaked in as the bind key"; return; }
    pass "bind key is the provided value, never a comment line"
}

# ── Test 4: `curl | bash` invocation form — script piped on stdin ──
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

# ── Test 6: interactive prompts must read from /dev/tty, never fd 0 ──
test_prompts_read_from_tty() {
    local prompt_reads; prompt_reads="$(grep -nE 'read -r -p' "$INSTALL_SH" || true)"
    [[ -n "$prompt_reads" ]] || { fail "tty: found interactive read prompts"; return; }

    local missing; missing="$(grep -nE 'read -r -p' "$INSTALL_SH" | grep -v '< */dev/tty' || true)"
    [[ -z "$missing" ]] || { fail "tty: every prompt reads from /dev/tty" "offending: ${missing}"; return; }
    pass "every interactive prompt reads from /dev/tty"
}

# ── Test 7: must not globally redirect stdin (the curl|bash hang regression) ──
test_no_global_stdin_redirect() {
    grep -qE 'exec[[:space:]]*<[[:space:]]*/dev/tty' "$INSTALL_SH" \
        && { fail "no-exec: \`exec < /dev/tty\` steals the script stream under curl|bash"; return; }
    pass "does not globally redirect stdin via exec < /dev/tty"
}

# ── Test 8: a non-WASM download (404 body) is rejected, not installed ──
test_rejects_invalid_wasm_download() {
    local box; box="$(make_sandbox)"
    STUB_MODE=bad_wasm run_install "$box" /dev/null --non-interactive --api-key sk-test
    local rc=$?

    [[ $rc -ne 0 ]] || { fail "bad wasm: install must abort on invalid download"; return; }
    grep -qi 'invalid' "${box}/stdout.log" || { fail "bad wasm: reports the wasm is invalid"; return; }
    pass "rejects an invalid (non-WASM) download instead of installing it"
}

# ── Test 9: the multi-file sidecar package is downloaded and extracted ──
test_extracts_sidecar_package() {
    local box; box="$(make_sandbox)"
    run_install "$box" /dev/null --non-interactive --api-key sk-test --keybind "Ctrl y"

    [[ -f "${box}/plugins/zellij_whisper_talk.wasm" ]] || { fail "sidecar: wasm present"; return; }
    [[ -f "${box}/plugins/transcribe.py" ]] || { fail "sidecar: transcribe.py extracted"; return; }
    [[ -f "${box}/plugins/whisper_sidecar/config.py" ]] || { fail "sidecar: whisper_sidecar package extracted"; return; }
    pass "downloads and extracts the sidecar package (transcribe.py + whisper_sidecar/)"
}

# ── Test 10: every curl download uses --fail (-f) so HTTP errors abort ──
test_downloads_use_curl_fail() {
    local bad; bad="$(grep -nE 'curl -' "$INSTALL_SH" | grep -v -- '-f' || true)"
    [[ -z "$bad" ]] || { fail "curl --fail: every curl download must use -f" "offending: ${bad}"; return; }
    pass "all curl downloads use --fail (-f) so HTTP errors abort"
}

echo "Running install.sh tests..."
test_fresh_config_creates_single_keybinds_block
test_inserts_into_existing_keybinds_block
test_keybind_is_the_provided_key
test_curl_pipe_form_non_interactive
test_already_configured_is_skipped
test_prompts_read_from_tty
test_no_global_stdin_redirect
test_rejects_invalid_wasm_download
test_extracts_sidecar_package
test_downloads_use_curl_fail

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"
if [[ "$FAIL" -gt 0 ]]; then
    printf '  - %s\n' "${FAILED_NAMES[@]}"
    exit 1
fi
