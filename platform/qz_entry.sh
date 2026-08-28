#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_ROOT="/inspire/hdd/project/long-working-agent/ky26299"
EXPECTED_MINISWE_VERSION="v2.4.6"
LLMROUTER_BASE_URL_VALUE="http://106.15.124.164:4000/v1"

die() { echo "ERROR: $*" >&2; exit 2; }
on_error() {
    local status=$?
    echo "QZ_ENTRY_FAILED action=${ACTION:-unknown} line=${BASH_LINENO[0]:-unknown} exit=$status" >&2
    exit "$status"
}
trap on_error ERR

require_root() {
    ROOT="${1:-}"
    [[ "$ROOT" == "$EXPECTED_ROOT" ]] || die "unexpected project root: $ROOT"
    [[ -d "$ROOT" ]] || die "project root is missing: $ROOT"
}

set_release_paths() {
    RELEASE="${1:-}"
    [[ "$RELEASE" =~ ^[A-Za-z0-9._-]+$ ]] || die "unsafe release id"
    RUNNER="$ROOT/code/mls-lite-runner-$RELEASE"
    MLS="$RUNNER/deps/MLS-Bench"
    AGENT="$RUNNER/deps/mini-swe-agent-$EXPECTED_MINISWE_VERSION"
    PYTHON="$RUNNER/runtime/env/bin/python"
    BASE_CONFIG="$RUNNER/runtime/config/miniswe_bash.yaml"
    READY_MARKER="$RUNNER/runtime/records/PREPARE_RELEASE_OK"
    LOCK="$RUNNER/runtime/locks/mls-prepare.lock"
}

round_root() { printf '%s\n' "$RUNNER/runtime/rounds/round-$1"; }
round_config() { printf '%s\n' "$(round_root "$1")/config/miniswe_bash.yaml"; }

require_release() {
    set_release_paths "$1"
    [[ -d "$RUNNER/.git" ]] || die "runner is missing: $RUNNER"
    [[ -d "$MLS/.git" ]] || die "release MLS repository is missing: $MLS"
    [[ -d "$AGENT/.git" ]] || die "release mini-SWE repository is missing: $AGENT"
    [[ -x "$PYTHON" ]] || die "release Python is missing: $PYTHON"
    [[ -f "$BASE_CONFIG" ]] || die "release base config is missing: $BASE_CONFIG"
    [[ -f "$READY_MARKER" ]] || die "release is incomplete; marker is missing: $READY_MARKER"
    local round
    for round in 1 2 3 4 5; do
        [[ -f "$(round_config "$round")" ]] || die "round $round config is missing"
        [[ -f "$(round_root "$round")/state.json" ]] || die "round $round state is missing"
    done
}

python_is_bootstrap_compatible() {
    local candidate="$1"
    [[ -x "$candidate" ]] || return 1
    "$candidate" -c 'import ensurepip, sys, venv; assert sys.version_info >= (3, 10), sys.version' >/dev/null 2>&1
}

bootstrap_candidates() {
    printf '%s\n' \
        "$ROOT/runtime/envs/mlsbench-lite-agent-v001/bin/python" \
        "$ROOT/runtime/envs/mlsbench-lite-agent/bin/python"
    local command_name candidate
    for command_name in python3.11 python3.10 python3 python; do
        candidate="$(command -v "$command_name" 2>/dev/null || true)"
        [[ -n "$candidate" ]] && printf '%s\n' "$candidate"
    done
}

select_bootstrap_python() {
    local requested="${1:-auto}" candidate
    if [[ "$requested" != auto ]]; then
        [[ "$requested" == "$ROOT"/* ]] || die "explicit bootstrap Python must stay under project root"
        python_is_bootstrap_compatible "$requested" || die "bootstrap Python is missing, below 3.10, or lacks venv: $requested"
        printf '%s\n' "$requested"
        return
    fi
    while IFS= read -r candidate; do
        if python_is_bootstrap_compatible "$candidate"; then
            printf '%s\n' "$candidate"
            return
        fi
    done < <(bootstrap_candidates)
    die "no Python >=3.10 with venv support; checked existing MLS venvs and system python commands"
}

probe_bootstrap() {
    require_root "$1"
    local report="$ROOT/code/mls-lite-python-bootstrap-probe.txt"
    local temporary="$report.tmp.$$" candidate version selected=""
    mkdir -p "$ROOT/code"
    : > "$temporary"
    while IFS= read -r candidate; do
        printf 'CANDIDATE=%s\n' "$candidate" >> "$temporary"
        if [[ -x "$candidate" ]]; then
            version="$($candidate -c 'import sys; print(sys.version.replace("\n", " "))' 2>&1 || true)"
            printf 'VERSION=%s\n' "$version" >> "$temporary"
            if python_is_bootstrap_compatible "$candidate"; then
                printf 'COMPATIBLE=yes\n' >> "$temporary"
                [[ -n "$selected" ]] || selected="$candidate"
            else
                printf 'COMPATIBLE=no\n' >> "$temporary"
            fi
        else
            printf 'EXISTS=no\n' >> "$temporary"
        fi
    done < <(bootstrap_candidates)
    printf 'SELECTED=%s\n' "${selected:-NONE}" >> "$temporary"
    mv "$temporary" "$report"
    cat "$report"
    [[ -n "$selected" ]] || die "no compatible bootstrap Python; see $report"
    echo BOOTSTRAP_PROBE_OK
}

create_release_env() {
    local requested_bootstrap="${1:-auto}" bootstrap
    [[ ! -e "$RUNNER/runtime/env" ]] || die "refusing to reuse partial release env: $RUNNER/runtime/env"
    bootstrap="$(select_bootstrap_python "$requested_bootstrap")"
    printf '%s\n' "$bootstrap" > "$RUNNER/runtime/records/bootstrap-python.path"
    "$bootstrap" -c 'import sys; print(sys.executable); print(sys.version)'
    "$bootstrap" -m venv --copies "$RUNNER/runtime/env"
    [[ -x "$PYTHON" ]] || die "venv creation did not produce $PYTHON"
    "$PYTHON" -c 'import sys; assert sys.version_info >= (3, 10), sys.version; print(sys.version)'
    "$PYTHON" -m ensurepip --upgrade
}

set_api_environment() {
    local api_key_env="$1" api_key="$2"
    [[ "$api_key_env" =~ ^[A-Z][A-Z0-9_]+$ ]] || die "unsafe API key variable"
    [[ -n "$api_key" ]] || die "API key is empty"
    export LLMROUTER_BASE_URL="http://106.15.124.164:4000/v1"
    printf -v "$api_key_env" '%s' "$api_key"
    export "$api_key_env"
}

check_gpu_host() {
    local expected="$1" count devices
    [[ "$expected" =~ ^(1|2|4|8)$ ]] || die "unsupported GPU count: $expected"
    count="$(nvidia-smi -L | tee /dev/stderr | wc -l | tr -d ' ')"
    devices="$(nvidia-smi --query-gpu=index --format=csv,noheader | paste -sd, - | tr -d ' ')"
    echo "VISIBLE_GPUS=$count EXPECTED_GPUS=$expected CUDA_VISIBLE_DEVICES=$devices"
    [[ "$count" == "$expected" ]] || die "visible GPU count mismatch"
    [[ "$(awk -F, '{print NF}' <<< "$devices")" == "$expected" ]] || die "GPU index count mismatch"
    export CUDA_VISIBLE_DEVICES="$devices"
    curl -sS -I --connect-timeout 10 --max-time 20 https://github.com/ >/dev/null
    command -v docker >/dev/null || die "docker CLI is missing"
    docker info >/dev/null
}

prepare_release() {
    require_root "$1"
    local github_url="$2" git_ref="$3" release="$4" expected_mls_commit="$5" mini_version="$6" bootstrap_python="${7:-auto}"
    [[ "$github_url" =~ ^https://github.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(.git)?$ ]] || die "unsafe GitHub URL"
    [[ "$git_ref" =~ ^[A-Za-z0-9._/-]+$ ]] || die "unsafe Git ref"
    [[ "$release" =~ ^[A-Za-z0-9._-]+$ ]] || die "unsafe release id"
    [[ "$expected_mls_commit" =~ ^[0-9a-f]{40}$ ]] || die "invalid MLS commit"
    [[ "$mini_version" == "$EXPECTED_MINISWE_VERSION" ]] || die "mini-SWE must be $EXPECTED_MINISWE_VERSION"

    local runner="$ROOT/code/mls-lite-runner-$release"
    mkdir -p "$ROOT/code"
    if [[ -e "$runner" ]]; then
        [[ -d "$runner/.git" ]] || die "existing release path is not a Git repository: $runner"
        [[ ! -f "$runner/runtime/records/PREPARE_RELEASE_OK" ]] || die "release is already complete: $runner"
        git -C "$runner" rev-parse --verify "$git_ref^{commit}" >/dev/null 2>&1 || die "existing runner does not contain requested ref: $git_ref"
        [[ "$(git -C "$runner" rev-parse HEAD)" == "$(git -C "$runner" rev-parse "$git_ref^{commit}")" ]] || die "existing runner HEAD does not match $git_ref"
        echo "RESUME_INCOMPLETE_RELEASE=$runner"
    else
        git clone --branch "$git_ref" --depth 1 "$github_url" "$runner"
    fi
    set_release_paths "$release"
    mkdir -p "$RUNNER/deps" "$RUNNER/runtime/cache/pip" "$RUNNER/runtime/records"

    if [[ -e "$MLS" ]]; then
        [[ -d "$MLS/.git" ]] || die "existing MLS path is not a Git repository"
    else
        git clone https://github.com/Imbernoulli/MLS-Bench.git "$MLS"
        git -C "$MLS" checkout "$expected_mls_commit"
    fi
    [[ "$(git -C "$MLS" rev-parse HEAD)" == "$expected_mls_commit" ]] || die "MLS HEAD mismatch"
    if [[ -e "$AGENT" ]]; then
        [[ -d "$AGENT/.git" ]] || die "existing mini-SWE path is not a Git repository"
    else
        git clone --branch "$mini_version" --depth 1 https://github.com/SWE-agent/mini-swe-agent.git "$AGENT"
    fi
    git -C "$AGENT" describe --tags --exact-match | grep -Fx "$mini_version"

    if [[ -x "$PYTHON" ]]; then
        "$PYTHON" -c 'import sys; assert sys.version_info >= (3, 10), sys.version'
        echo "REUSE_VALID_RELEASE_ENV=$PYTHON"
    else
        [[ ! -e "$RUNNER/runtime/env" ]] || die "partial release env exists but has no executable Python"
        create_release_env "$bootstrap_python"
    fi
    PIP_CACHE_DIR="$RUNNER/runtime/cache/pip" "$PYTHON" -m pip install --upgrade pip setuptools wheel

    local patch_file="$RUNNER/patches/mls-registration-clean.patch"
    sha256sum "$patch_file" > "$RUNNER/runtime/records/registration-patch.sha256"
    if [[ ! -f "$RUNNER/runtime/records/mls-status-before.txt" ]]; then
        git -C "$MLS" status --short > "$RUNNER/runtime/records/mls-status-before.txt"
    fi
    if git -C "$MLS" apply --check "$patch_file"; then
        git -C "$MLS" apply "$patch_file"
    elif git -C "$MLS" apply -R --check "$patch_file"; then
        echo "REGISTRATION_PATCH_ALREADY_APPLIED"
    else
        die "MLS registration patch is neither cleanly applicable nor already applied"
    fi
    git -C "$MLS" diff --binary > "$RUNNER/runtime/records/mls-registration.patch"

    PIP_CACHE_DIR="$RUNNER/runtime/cache/pip" "$PYTHON" -m pip install -e "${MLS}[agent]"
    PIP_CACHE_DIR="$RUNNER/runtime/cache/pip" "$PYTHON" -m pip install -e "$AGENT" numpy
    PIP_CACHE_DIR="$RUNNER/runtime/cache/pip" "$PYTHON" -m pip install -e "$RUNNER"
    "$PYTHON" -m pip check

    mkdir -p "$RUNNER/runtime/config" "$RUNNER/runtime/assets/receipts" "$RUNNER/runtime/rounds" "$RUNNER/runtime/locks"
    PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -m mlsbench.cli agent --help | grep -F miniswe-bash >/dev/null
    PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -m mls_lite_runner validate
    PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -m mls_lite_runner write-config \
        --output "$BASE_CONFIG" --save-path "$RUNNER/runtime/records/api-smoke" \
        --api-base "$LLMROUTER_BASE_URL_VALUE" --force

    local round root config
    for round in 1 2 3 4 5; do
        root="$(round_root "$round")"
        config="$(round_config "$round")"
        mkdir -p "$root/config" "$root/records"
        PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -m mls_lite_runner write-config \
            --output "$config" --save-path "$root/records" --api-base "$LLMROUTER_BASE_URL_VALUE" --force
        PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -m mls_lite_runner init-state \
            --state "$root/state.json" >/dev/null
        grep -Fx "      api_base: \"$LLMROUTER_BASE_URL_VALUE\"" "$config" >/dev/null
    done
    PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -c \
        'import mlsbench, mls_agent, minisweagent, numpy, yaml; print("HOST_IMPORTS_OK")'

    local marker_tmp="$READY_MARKER.tmp.$$"
    {
        printf 'release=%s\n' "$RELEASE"
        printf 'runner_commit=%s\n' "$(git -C "$RUNNER" rev-parse HEAD)"
        printf 'mls_commit=%s\n' "$(git -C "$MLS" rev-parse HEAD)"
        printf 'miniswe_version=%s\n' "$mini_version"
        printf 'python=%s\n' "$($PYTHON -c 'import sys; print(sys.version.split()[0])')"
        printf 'api_base=%s\n' "$LLMROUTER_BASE_URL_VALUE"
    } > "$marker_tmp"
    mv "$marker_tmp" "$READY_MARKER"
    echo "RELEASE_ROOT=$RUNNER"
    cat "$READY_MARKER"
    echo PREPARE_RELEASE_OK
}

host_smoke() {
    require_root "$1"; require_release "$2"; check_gpu_host "$3"
    PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -c \
        'import mlsbench, mls_agent, minisweagent, numpy; print("PYTHON_IMPORTS_OK")'
    echo HOST_SMOKE_OK
}

api_smoke() {
    require_root "$1"; require_release "$2"
    local model="$3" api_key_env="$4" api_key="$5"
    set_api_environment "$api_key_env" "$api_key"
    nvidia-smi -L
    curl -sS -I --connect-timeout 10 --max-time 20 https://github.com/ >/dev/null
    PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -m mls_lite_runner api-smoke \
        --config "$BASE_CONFIG" --model "$model"
    echo API_JOB_OK
}

run_task() {
    require_root "$1"; require_release "$2"
    local task="$3" model="$4" round="$5" expected_gpus="$6" api_key_env="$7" api_key="$8"
    [[ "$task" =~ ^[a-z0-9-]+$ ]] || die "unsafe task id"
    [[ "$round" =~ ^[1-5]$ ]] || die "invalid round"
    set_api_environment "$api_key_env" "$api_key"
    check_gpu_host "$expected_gpus"
    local root="$(round_root "$round")" config="$(round_config "$round")"
    PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -m mls_lite_runner doctor \
        --round "$round" --mls-root "$MLS" --agent-root "$RUNNER" --python "$PYTHON"
    PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -m mls_lite_runner run-task "$task" \
        --mls-root "$MLS" --python "$PYTHON" --config "$config" --model "$model" \
        --runtime-root "$root/execution" --state "$root/state.json" \
        --network-mode online --preflight-report "$RUNNER/reports/lite30-preflight.json" \
        --asset-manifest-dir "$RUNNER/manifests/task-assets" --asset-source-root "$MLS" \
        --asset-receipt-root "$RUNNER/runtime/assets/receipts" --prepare-lock "$LOCK" --execute
    echo RUN_TASK_OK
}

run_round() {
    require_root "$1"; require_release "$2"
    local round="$3" model="$4" expected_gpus="$5" api_key_env="$6" api_key="$7" task_csv="$8" retry_failed="${9:-0}"
    [[ "$round" =~ ^[1-5]$ ]] || die "invalid round"
    [[ "$retry_failed" =~ ^[01]$ ]] || die "retry flag must be 0 or 1"
    [[ "$task_csv" =~ ^[a-z0-9-]+(,[a-z0-9-]+)*$ ]] || die "invalid task list"
    set_api_environment "$api_key_env" "$api_key"
    check_gpu_host "$expected_gpus"
    local root="$(round_root "$round")" config="$(round_config "$round")"
    local retry_args=() tasks=()
    IFS=',' read -r -a tasks <<< "$task_csv"
    [[ "$retry_failed" == 1 ]] && retry_args+=(--retry-failed)
    PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -m mls_lite_runner doctor \
        --round "$round" --mls-root "$MLS" --agent-root "$RUNNER" --python "$PYTHON"
    PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -m mls_lite_runner run-round \
        --round "$round" --tasks "${tasks[@]}" --mls-root "$MLS" --python "$PYTHON" \
        --config "$config" --model "$model" --runtime-root "$root/execution" \
        --state "$root/state.json" --report "$root/report.json" \
        --network-mode online --preflight-report "$RUNNER/reports/lite30-preflight.json" \
        --asset-manifest-dir "$RUNNER/manifests/task-assets" --asset-source-root "$MLS" \
        --asset-receipt-root "$RUNNER/runtime/assets/receipts" --prepare-lock "$LOCK" \
        "${retry_args[@]}" --execute
    echo RUN_ROUND_ORCHESTRATION_OK
}

report_round() {
    require_root "$1"; require_release "$2"
    local round="$3" root
    [[ "$round" =~ ^[1-5]$ ]] || die "invalid round"
    root="$(round_root "$round")"
    [[ -f "$root/report.json" ]] || die "round report does not exist"
    echo "REPORT_FILE=$root/report.json"
    "$PYTHON" -m json.tool "$root/report.json"
    echo REPORT_OK
}

main() {
    ACTION="${1:-}"
    shift || true
    case "$ACTION" in
        probe-bootstrap) probe_bootstrap "$@" ;;
        prepare-release) prepare_release "$@" ;;
        host-smoke) host_smoke "$@" ;;
        api-smoke) api_smoke "$@" ;;
        run-task) run_task "$@" ;;
        run-round) run_round "$@" ;;
        report) report_round "$@" ;;
        *) die "usage: qz_entry.sh {probe-bootstrap|prepare-release|host-smoke|api-smoke|run-task|run-round|report} ..." ;;
    esac
}

main "$@"
