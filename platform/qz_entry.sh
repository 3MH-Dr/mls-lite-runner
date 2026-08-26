#!/usr/bin/env bash
set -euo pipefail

EXPECTED_ROOT="/inspire/hdd/project/long-working-agent/ky26299"
LLMROUTER_BASE_URL_VALUE="http://106.15.124.164:4000/v1"

die() { echo "ERROR: $*" >&2; exit 2; }

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
    AGENT="$RUNNER/deps/mini-swe-agent-v2.4.6"
    PYTHON="$RUNNER/runtime/env/bin/python"
    CONFIG="$RUNNER/runtime/config/miniswe_bash.yaml"
    LOCK="$RUNNER/runtime/locks/mls-prepare.lock"
}

require_release() {
    set_release_paths "$1"
    [[ -d "$RUNNER/.git" ]] || die "runner is missing: $RUNNER"
    [[ -d "$MLS/.git" ]] || die "release MLS repository is missing: $MLS"
    [[ -d "$AGENT/.git" ]] || die "release mini-SWE repository is missing: $AGENT"
    [[ -x "$PYTHON" ]] || die "release Python is missing: $PYTHON"
    [[ -f "$CONFIG" ]] || die "release config is missing: $CONFIG"
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
    local github_url="$2" git_ref="$3" release="$4" expected_mls_commit="$5" mini_version="$6"
    [[ "$github_url" =~ ^https://github.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(.git)?$ ]] || die "unsafe GitHub URL"
    [[ "$git_ref" =~ ^[A-Za-z0-9._/-]+$ ]] || die "unsafe Git ref"
    [[ "$release" =~ ^[A-Za-z0-9._-]+$ ]] || die "unsafe release id"
    [[ "$expected_mls_commit" =~ ^[0-9a-f]{40}$ ]] || die "invalid MLS commit"
    [[ "$mini_version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "invalid mini-SWE version"

    local runner="$ROOT/code/mls-lite-runner-$release"
    [[ ! -e "$runner" ]] || die "release path already exists; use a new release id: $runner"
    mkdir -p "$ROOT/code"
    git clone --branch "$git_ref" --depth 1 "$github_url" "$runner"
    set_release_paths "$release"
    AGENT="$RUNNER/deps/mini-swe-agent-$mini_version"
    mkdir -p "$RUNNER/deps" "$RUNNER/runtime/cache/pip" "$RUNNER/runtime/records"

    git clone https://github.com/Imbernoulli/MLS-Bench.git "$MLS"
    git -C "$MLS" checkout "$expected_mls_commit"
    [[ "$(git -C "$MLS" rev-parse HEAD)" == "$expected_mls_commit" ]] || die "MLS HEAD mismatch"
    git clone --branch "$mini_version" --depth 1 https://github.com/SWE-agent/mini-swe-agent.git "$AGENT"
    git -C "$AGENT" describe --tags --exact-match | grep -Fx "$mini_version"

    if command -v conda >/dev/null; then
        conda create -p "$RUNNER/runtime/env" python=3.11 pip -y
    elif command -v python3.11 >/dev/null; then
        python3.11 -m venv "$RUNNER/runtime/env"
    else
        die "need conda or python3.11"
    fi
    "$PYTHON" -c 'import sys; assert sys.version_info[:2] == (3, 11), sys.version'
    PIP_CACHE_DIR="$RUNNER/runtime/cache/pip" "$PYTHON" -m pip install --upgrade pip setuptools wheel
    PIP_CACHE_DIR="$RUNNER/runtime/cache/pip" "$PYTHON" -m pip install -e "${MLS}[agent]"
    PIP_CACHE_DIR="$RUNNER/runtime/cache/pip" "$PYTHON" -m pip install -e "$AGENT" numpy

    local patch_file="$RUNNER/patches/mls-registration-clean.patch"
    sha256sum "$patch_file" > "$RUNNER/runtime/records/registration-patch.sha256"
    git -C "$MLS" status --short > "$RUNNER/runtime/records/mls-status-before.txt"
    git -C "$MLS" apply --check "$patch_file"
    git -C "$MLS" apply "$patch_file"
    git -C "$MLS" diff --binary > "$RUNNER/runtime/records/mls-registration.patch"

    PIP_CACHE_DIR="$RUNNER/runtime/cache/pip" "$PYTHON" -m pip install -e "$RUNNER"
    "$PYTHON" -m pip check
    mkdir -p "$RUNNER/runtime/config" "$RUNNER/runtime/execution" "$RUNNER/runtime/assets/receipts" \
        "$RUNNER/runtime/rounds" "$RUNNER/runtime/locks"
    PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -m mlsbench.cli agent --help | grep -F miniswe-bash >/dev/null
    PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -m mls_lite_runner validate
    PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -m mls_lite_runner write-config \
        --output "$CONFIG" --save-path "$RUNNER/runtime/records" \
        --api-base "$LLMROUTER_BASE_URL_VALUE"
    local round
    for round in 1 2 3 4 5; do
        mkdir -p "$RUNNER/runtime/rounds/round-$round"
        PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -m mls_lite_runner init-state \
            --state "$RUNNER/runtime/rounds/round-$round/state.json" >/dev/null
    done
    PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -c \
        'import mlsbench, mls_agent, minisweagent, numpy, yaml; print("HOST_IMPORTS_OK")'
    echo "RELEASE_ROOT=$RUNNER"
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
        --config "$CONFIG" --model "$model"
    echo API_JOB_OK
}

run_task() {
    require_root "$1"; require_release "$2"
    local task="$3" model="$4" round="$5" expected_gpus="$6" api_key_env="$7" api_key="$8"
    [[ "$task" =~ ^[a-z0-9-]+$ ]] || die "unsafe task id"
    [[ "$round" =~ ^[1-5]$ ]] || die "invalid round"
    set_api_environment "$api_key_env" "$api_key"
    check_gpu_host "$expected_gpus"
    local round_root="$RUNNER/runtime/rounds/round-$round"
    PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -m mls_lite_runner doctor \
        --round "$round" --mls-root "$MLS" --agent-root "$RUNNER" --python "$PYTHON"
    PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -m mls_lite_runner run-task "$task" \
        --mls-root "$MLS" --python "$PYTHON" --config "$CONFIG" --model "$model" \
        --runtime-root "$round_root/execution" --state "$round_root/state.json" \
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
    local round_root="$RUNNER/runtime/rounds/round-$round"
    local retry_args=() tasks=()
    IFS=',' read -r -a tasks <<< "$task_csv"
    [[ "$retry_failed" == 1 ]] && retry_args+=(--retry-failed)
    PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -m mls_lite_runner doctor \
        --round "$round" --mls-root "$MLS" --agent-root "$RUNNER" --python "$PYTHON"
    PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -m mls_lite_runner run-round \
        --round "$round" --tasks "${tasks[@]}" --mls-root "$MLS" --python "$PYTHON" \
        --config "$CONFIG" --model "$model" --runtime-root "$round_root/execution" \
        --state "$round_root/state.json" --report "$round_root/report.json" \
        --network-mode online --preflight-report "$RUNNER/reports/lite30-preflight.json" \
        --asset-manifest-dir "$RUNNER/manifests/task-assets" --asset-source-root "$MLS" \
        --asset-receipt-root "$RUNNER/runtime/assets/receipts" --prepare-lock "$LOCK" \
        "${retry_args[@]}" --execute
    echo RUN_ROUND_ORCHESTRATION_OK
}

report_round() {
    require_root "$1"; require_release "$2"
    local round="$3" round_root="$RUNNER/runtime/rounds/round-$3"
    [[ "$round" =~ ^[1-5]$ ]] || die "invalid round"
    [[ -f "$round_root/report.json" ]] || die "round report does not exist"
    echo "REPORT_FILE=$round_root/report.json"
    "$PYTHON" -m json.tool "$round_root/report.json"
    echo REPORT_OK
}

ACTION="${1:-}"; shift || true
case "$ACTION" in
    prepare-release) prepare_release "$@" ;;
    host-smoke) host_smoke "$@" ;;
    api-smoke) api_smoke "$@" ;;
    run-task) run_task "$@" ;;
    run-round) run_round "$@" ;;
    report) report_round "$@" ;;
    *) die "usage: qz_entry.sh {prepare-release|host-smoke|api-smoke|run-task|run-round|report} ..." ;;
esac
