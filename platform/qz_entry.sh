#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_ROOT="/inspire/hdd/project/long-working-agent/ky26299"
EXPECTED_MINISWE_VERSION="v2.4.6"
LLMROUTER_BASE_URL_VALUE="http://106.15.124.164:4000/v1"

die() { echo "ERROR: $*" >&2; exit 2; }
on_error() {
    local status=$?
    if [[ -n "${ENV_STATUS_FILE:-}" && -f "$ENV_STATUS_FILE" && "$(<"$ENV_STATUS_FILE")" == UPDATING ]]; then
        printf 'FAILED\n' > "$ENV_STATUS_FILE.tmp.$$" || true
        mv "$ENV_STATUS_FILE.tmp.$$" "$ENV_STATUS_FILE" || true
    fi
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
    SHARED_ENV="$ROOT/runtime/envs/mlsbench-lite-agent"
    PYTHON="$SHARED_ENV/bin/python"
    ENV_REGISTRY="$ROOT/runtime/env-registry/mlsbench-lite-agent"
    ENV_STATUS_FILE="$ENV_REGISTRY/status"
    ENV_LOCK="$ROOT/runtime/locks/mlsbench-lite-agent.lock"
    ENV_RECEIPT="$RUNNER/runtime/records/environment-receipt.json"
    BASE_CONFIG="$RUNNER/runtime/config/miniswe_bash.yaml"
    READY_MARKER="$RUNNER/runtime/records/PREPARE_RELEASE_OK"
    LOCK="$RUNNER/runtime/locks/mls-prepare.lock"
}

round_root() { printf '%s\n' "$RUNNER/runtime/rounds/round-$1"; }
round_config() { printf '%s\n' "$(round_root "$1")/config/miniswe_bash.yaml"; }
release_pythonpath() { printf '%s\n' "$RUNNER/src:$MLS/src:$AGENT/src"; }

run_release_python() {
    local pythonpath
    pythonpath="$(release_pythonpath)"
    env -u LD_LIBRARY_PATH \
        PYTHONPATH="$pythonpath" \
        PYTHONDONTWRITEBYTECODE=1 \
        PYTHONNOUSERSITE=1 \
        "$PYTHON" "$@"
}

require_shared_python() {
    [[ -x "$PYTHON" ]] || die "shared environment Python is missing: $PYTHON"
    PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 "$PYTHON" -c \
        'import sys; assert sys.version_info >= (3, 10), sys.version; print("SHARED_PYTHON=" + sys.executable); print("SHARED_VERSION=" + sys.version.split()[0])'
    PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 "$PYTHON" -m pip --version
}

require_release() {
    set_release_paths "$1"
    [[ -d "$RUNNER/.git" ]] || die "runner is missing: $RUNNER"
    [[ -d "$MLS/.git" ]] || die "release MLS repository is missing: $MLS"
    [[ -d "$AGENT/.git" ]] || die "release mini-SWE repository is missing: $AGENT"
    require_shared_python
    [[ -f "$ENV_RECEIPT" ]] || die "shared environment receipt is missing: $ENV_RECEIPT"
    [[ -f "$ENV_STATUS_FILE" ]] || die "shared environment status is missing"
    [[ "$(<"$ENV_STATUS_FILE")" == READY ]] || die "shared environment is not READY"
    exec 8>"$ENV_LOCK"
    flock -s -n 8 || die "shared environment is being modified"
    [[ -f "$BASE_CONFIG" ]] || die "release base config is missing: $BASE_CONFIG"
    [[ -f "$READY_MARKER" ]] || die "release is incomplete; marker is missing: $READY_MARKER"
    local round
    for round in 1 2 3 4 5; do
        [[ -f "$(round_config "$round")" ]] || die "round $round config is missing"
        [[ -f "$(round_root "$round")/state.json" ]] || die "round $round state is missing"
    done
}

snapshot_environment() {
    local destination="$1" temporary="$1.tmp.$$"
    rm -rf -- "$temporary"
    mkdir -p "$temporary"
    PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 "$PYTHON" -m pip list --format=json > "$temporary/pip-list.json"
    PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 "$PYTHON" -m pip list --editable --format=json > "$temporary/editable-projects.json"
    if PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 "$PYTHON" -m pip check > "$temporary/pip-check.txt" 2>&1; then
        printf 'ok\n' > "$temporary/pip-check.status"
    else
        printf 'failed\n' > "$temporary/pip-check.status"
    fi
    PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 "$PYTHON" -c \
        'import json, platform, sys; print(json.dumps({"executable": sys.executable, "python": platform.python_version(), "prefix": sys.prefix}, sort_keys=True))' \
        > "$temporary/python.json"
    sha256sum "$temporary/pip-list.json" "$temporary/editable-projects.json" "$temporary/python.json" \
        > "$temporary/fingerprint.sha256"
    rm -rf -- "$destination"
    mv "$temporary" "$destination"
}

probe_shared_environment() {
    require_root "$1"
    set_release_paths "shared-probe"
    local report temporary module
    report="$ROOT/code/mls-lite-shared-env-probe.txt"
    temporary="$report.tmp.$$"
    mkdir -p "$ROOT/code" "$ROOT/runtime/locks"
    : > "$temporary"
    {
        printf 'SHARED_ENV=%s\n' "$SHARED_ENV"
        du -sh "$SHARED_ENV" 2>/dev/null || true
        require_shared_python
        if PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 "$PYTHON" -m pip check; then
            printf 'PIP_CHECK=ok\n'
        else
            printf 'PIP_CHECK=failed\n'
        fi
        for module in yaml numpy mlsbench minisweagent; do
            if PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 "$PYTHON" -c "import $module" >/dev/null 2>&1; then
                printf 'IMPORT_%s=yes\n' "$module"
            else
                printf 'IMPORT_%s=no\n' "$module"
            fi
        done
        printf 'SHARED_ENV_BASE_OK\n'
    } 2>&1 | tee "$temporary"
    mv "$temporary" "$report"
    echo "PROBE_REPORT=$report"
}

configure_shared_environment() {
    local allow_change="$1" transaction_id transaction changed="none" pythonpath
    [[ "$allow_change" =~ ^[01]$ ]] || die "allow environment change must be 0 or 1"
    require_shared_python
    command -v flock >/dev/null || die "flock is required for shared environment safety"
    mkdir -p "$ROOT/runtime/locks" "$ENV_REGISTRY/baseline" "$ENV_REGISTRY/transactions" "$ENV_REGISTRY/consumers"
    exec 9>"$ENV_LOCK"
    flock -x 9
    printf 'UPDATING\n' > "$ENV_STATUS_FILE.tmp.$$"
    mv "$ENV_STATUS_FILE.tmp.$$" "$ENV_STATUS_FILE"
    if [[ ! -f "$ENV_REGISTRY/baseline/pip-list.json" ]]; then
        snapshot_environment "$ENV_REGISTRY/baseline"
    fi
    transaction_id="$RELEASE-$(git -C "$RUNNER" rev-parse --short=12 HEAD)-${QZ_RUN_DIR##*/}"
    [[ "$transaction_id" =~ ^[A-Za-z0-9._-]+$ ]] || die "unsafe environment transaction id"
    transaction="$ENV_REGISTRY/transactions/$transaction_id"
    [[ ! -e "$transaction" ]] || die "environment transaction already exists: $transaction"
    mkdir -p "$transaction"
    snapshot_environment "$transaction/before"
    pythonpath="$(release_pythonpath)"
    local host_requirements="$RUNNER/manifests/host-requirements.txt"
    [[ -f "$host_requirements" ]] || die "host requirements manifest is missing"
    if ! PYTHONPATH="$pythonpath" PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 "$PYTHON" -c \
        'import importlib.metadata as m; import mlsbench, mls_agent, mls_lite_runner, minisweagent, numpy, yaml, litellm, torch, deap, pgmpy, causallearn; assert m.version("litellm") == "1.93.0"; assert torch.__version__ == "2.5.1+cpu"'; then
        changed="packages-installed"
        PIP_CACHE_DIR="$ROOT/runtime/cache/pip" PIP_DISABLE_PIP_VERSION_CHECK=1 \
            PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 "$PYTHON" -m pip install \
            --dry-run --report "$transaction/pip-plan.json" --upgrade-strategy only-if-needed \
            --extra-index-url https://download.pytorch.org/whl/cpu \
            "${MLS}[agent]" "$AGENT" "$RUNNER" numpy -r "$host_requirements"
        "$PYTHON" -c \
            'import json,sys; data=json.load(open(sys.argv[1],encoding="utf-8")); rows=sorted({"{}=={}".format(x["metadata"]["name"],x["metadata"]["version"]) for x in data.get("install",[])}); print("\n".join(rows))' \
            "$transaction/pip-plan.json" > "$transaction/resolved-constraints.txt"
        if [[ "$allow_change" != 1 ]]; then
            printf 'NEEDS_CHANGE\n' > "$ENV_STATUS_FILE.tmp.$$"
            mv "$ENV_STATUS_FILE.tmp.$$" "$ENV_STATUS_FILE"
            echo "ENVIRONMENT_PLAN=$transaction/pip-plan.json"
            die "shared environment needs packages; inspect the plan and rerun with allow-change=1"
        fi
        PIP_CACHE_DIR="$ROOT/runtime/cache/pip" PIP_DISABLE_PIP_VERSION_CHECK=1 \
            PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 "$PYTHON" -m pip install \
            --report "$transaction/pip-install.json" --upgrade-strategy only-if-needed \
            --extra-index-url https://download.pytorch.org/whl/cpu \
            --constraint "$transaction/resolved-constraints.txt" \
            "${MLS}[agent]" "$AGENT" "$RUNNER" numpy -r "$host_requirements"
    fi
    PYTHONPATH="$pythonpath" PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 "$PYTHON" -c \
        'import importlib.metadata as m; import mlsbench, mls_agent, mls_lite_runner, minisweagent, numpy, yaml, litellm, torch, deap, pgmpy, causallearn; assert m.version("litellm") == "1.93.0"; assert torch.__version__ == "2.5.1+cpu"; print("SHARED_ENV_IMPORTS_OK")'
    PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 "$PYTHON" -m pip check
    snapshot_environment "$transaction/after"
    local runner_commit mls_commit fingerprint consumer temporary
    runner_commit="$(git -C "$RUNNER" rev-parse HEAD)"
    mls_commit="$(git -C "$MLS" rev-parse HEAD)"
    fingerprint="$(sha256sum "$transaction/after/pip-list.json" | awk '{print $1}')"
    consumer="$ENV_REGISTRY/consumers/mls-lite-runner-$RELEASE.json"
    temporary="$consumer.tmp.$$"
    "$PYTHON" -c \
        'import json,sys; keys=("consumer","shared_env","python","runner_commit","mls_commit","miniswe_version","transaction","change","package_fingerprint"); print(json.dumps(dict(zip(keys,sys.argv[1:])),indent=2,sort_keys=True))' \
        "mls-lite-runner-$RELEASE" "$SHARED_ENV" "$PYTHON" "$runner_commit" "$mls_commit" \
        "$EXPECTED_MINISWE_VERSION" "$transaction_id" "$changed" "$fingerprint" > "$temporary"
    mv "$temporary" "$consumer"
    cp "$consumer" "$ENV_RECEIPT.tmp.$$"
    mv "$ENV_RECEIPT.tmp.$$" "$ENV_RECEIPT"
    printf 'READY\n' > "$ENV_STATUS_FILE.tmp.$$"
    mv "$ENV_STATUS_FILE.tmp.$$" "$ENV_STATUS_FILE"
    echo "ENVIRONMENT_TRANSACTION=$transaction_id"
    echo "ENVIRONMENT_CHANGE=$changed"
    echo "ENVIRONMENT_RECEIPT=$ENV_RECEIPT"
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
    local github_url="$2" git_ref="$3" release="$4" expected_mls_commit="$5" mini_version="$6" allow_env_change="${7:-0}"
    [[ "$github_url" =~ ^https://github.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(.git)?$ ]] || die "unsafe GitHub URL"
    [[ "$git_ref" =~ ^[A-Za-z0-9._/-]+$ ]] || die "unsafe Git ref"
    [[ "$release" =~ ^[A-Za-z0-9._-]+$ ]] || die "unsafe release id"
    [[ "$expected_mls_commit" =~ ^[0-9a-f]{40}$ ]] || die "invalid MLS commit"
    [[ "$mini_version" == "$EXPECTED_MINISWE_VERSION" ]] || die "mini-SWE must be $EXPECTED_MINISWE_VERSION"
    [[ "$allow_env_change" =~ ^[01]$ ]] || die "allow environment change must be 0 or 1"

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
    mkdir -p "$RUNNER/deps" "$RUNNER/runtime/records" "$ROOT/runtime/cache/pip"

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

    configure_shared_environment "$allow_env_change"

    mkdir -p "$RUNNER/runtime/config" "$RUNNER/runtime/assets/receipts" "$RUNNER/runtime/rounds" "$RUNNER/runtime/locks"
    local pythonpath="$(release_pythonpath)"
    PYTHONPATH="$pythonpath" PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 "$PYTHON" -m mlsbench.cli agent --help | grep -F miniswe-bash >/dev/null
    PYTHONPATH="$pythonpath" PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 "$PYTHON" -m mls_lite_runner validate
    PYTHONPATH="$pythonpath" PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 "$PYTHON" -m mls_lite_runner write-config \
        --output "$BASE_CONFIG" --save-path "$RUNNER/runtime/records/api-smoke" \
        --api-base "$LLMROUTER_BASE_URL_VALUE" --force

    local round root config
    for round in 1 2 3 4 5; do
        root="$(round_root "$round")"
        config="$(round_config "$round")"
        mkdir -p "$root/config" "$root/records"
        PYTHONPATH="$pythonpath" PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 "$PYTHON" -m mls_lite_runner write-config \
            --output "$config" --save-path "$root/records" --api-base "$LLMROUTER_BASE_URL_VALUE" --force
        ! grep -Eq '^[[:space:]]*data_root:' "$config" || die "generated config must not override MLS absolute data_root"
        PYTHONPATH="$pythonpath" PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 "$PYTHON" -m mls_lite_runner init-state \
            --state "$root/state.json" >/dev/null
        grep -Fx "      api_base: \"$LLMROUTER_BASE_URL_VALUE\"" "$config" >/dev/null
    done
    PYTHONPATH="$pythonpath" PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 "$PYTHON" -c \
        'import importlib.metadata as m; import mlsbench, mls_agent, minisweagent, numpy, yaml, litellm, torch, deap, pgmpy, causallearn; assert m.version("litellm") == "1.93.0"; assert torch.__version__ == "2.5.1+cpu"; print("HOST_IMPORTS_OK")'

    local marker_tmp="$READY_MARKER.tmp.$$"
    {
        printf 'release=%s\n' "$RELEASE"
        printf 'runner_commit=%s\n' "$(git -C "$RUNNER" rev-parse HEAD)"
        printf 'mls_commit=%s\n' "$(git -C "$MLS" rev-parse HEAD)"
        printf 'miniswe_version=%s\n' "$mini_version"
        printf 'python=%s\n' "$($PYTHON -c 'import sys; print(sys.version.split()[0])')"
        printf 'shared_env=%s\n' "$SHARED_ENV"
        printf 'environment_receipt=%s\n' "$ENV_RECEIPT"
        printf 'api_base=%s\n' "$LLMROUTER_BASE_URL_VALUE"
    } > "$marker_tmp"
    mv "$marker_tmp" "$READY_MARKER"
    echo "RELEASE_ROOT=$RUNNER"
    cat "$READY_MARKER"
    echo PREPARE_RELEASE_OK
}

host_smoke() {
    require_root "$1"; require_release "$2"; check_gpu_host "$3"
    run_release_python -c \
        'import importlib.metadata as m; import mlsbench, mls_agent, minisweagent, numpy, litellm, torch, deap, pgmpy, causallearn; assert m.version("litellm") == "1.93.0"; assert torch.__version__ == "2.5.1+cpu"; print("PYTHON_IMPORTS_OK")'
    echo HOST_SMOKE_OK
}

upgrade_release() {
    require_root "$1"
    set_release_paths "$2"
    local allow_env_change="${3:-0}"
    [[ -d "$RUNNER/.git" && -d "$MLS/.git" && -d "$AGENT/.git" ]] || die "release repositories are incomplete"
    [[ -f "$READY_MARKER" ]] || die "existing release completion marker is missing"
    configure_shared_environment "$allow_env_change"
    mkdir -p "$RUNNER/runtime/config"
    local pythonpath root config round
    pythonpath="$(release_pythonpath)"
    PYTHONPATH="$pythonpath" PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 "$PYTHON" -m mls_lite_runner write-config \
        --output "$BASE_CONFIG" --save-path "$RUNNER/runtime/records/api-smoke" \
        --api-base "$LLMROUTER_BASE_URL_VALUE" --force
    for round in 1 2 3 4 5; do
        root="$(round_root "$round")"
        config="$(round_config "$round")"
        mkdir -p "$root/config" "$root/records"
        PYTHONPATH="$pythonpath" PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 "$PYTHON" -m mls_lite_runner write-config \
            --output "$config" --save-path "$root/records" --api-base "$LLMROUTER_BASE_URL_VALUE" --force
        ! grep -Eq '^[[:space:]]*data_root:' "$config" || die "generated config still overrides data_root"
    done
    printf 'runner_commit=%s\nupgraded_at=%s\n' "$(git -C "$RUNNER" rev-parse HEAD)" "$(date -u +%FT%TZ)" \
        >> "$READY_MARKER"
    echo UPGRADE_RELEASE_OK
}

api_smoke() {
    require_root "$1"; require_release "$2"
    local model="$3" api_key_env="$4" api_key="$5"
    set_api_environment "$api_key_env" "$api_key"
    nvidia-smi -L
    curl -sS -I --connect-timeout 10 --max-time 20 https://github.com/ >/dev/null
    run_release_python -m mls_lite_runner api-smoke \
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
    echo HOST_PYTHON_LINKAGE=release-isolated
    run_release_python -m mls_lite_runner doctor \
        --round "$round" --mls-root "$MLS" --agent-root "$RUNNER" --python "$PYTHON"
    run_release_python -m mls_lite_runner run-task "$task" \
        --mls-root "$MLS" --python "$PYTHON" --config "$config" --model "$model" \
        --runtime-root "$root/execution" --state "$root/state.json" \
        --network-mode online --preflight-report "$RUNNER/reports/lite30-preflight.json" \
        --asset-manifest-dir "$RUNNER/manifests/task-assets" --asset-source-root "$MLS" \
        --asset-receipt-root "$RUNNER/runtime/assets/receipts" --prepare-lock "$LOCK" --execute
    echo RUN_TASK_OK
}

run_round() {
    require_root "$1"; require_release "$2"
    local round="$3" model="$4" expected_gpus="$5" api_key_env="$6" api_key="$7" task_csv="$8" retry_failed="${9:-0}" retry_partial="${10:-0}"
    [[ "$round" =~ ^[1-5]$ ]] || die "invalid round"
    [[ "$retry_failed" =~ ^[01]$ ]] || die "retry flag must be 0 or 1"
    [[ "$retry_partial" =~ ^[01]$ ]] || die "retry partial flag must be 0 or 1"
    [[ "$task_csv" =~ ^[a-z0-9-]+(,[a-z0-9-]+)*$ ]] || die "invalid task list"
    set_api_environment "$api_key_env" "$api_key"
    check_gpu_host "$expected_gpus"
    local root="$(round_root "$round")" config="$(round_config "$round")"
    local retry_args=() tasks=()
    IFS=',' read -r -a tasks <<< "$task_csv"
    [[ "$retry_failed" == 1 ]] && retry_args+=(--retry-failed)
    [[ "$retry_partial" == 1 ]] && retry_args+=(--retry-partial)
    echo HOST_PYTHON_LINKAGE=release-isolated
    run_release_python -m mls_lite_runner doctor \
        --round "$round" --mls-root "$MLS" --agent-root "$RUNNER" --python "$PYTHON"
    run_release_python -m mls_lite_runner run-round \
        --round "$round" --tasks "${tasks[@]}" --mls-root "$MLS" --python "$PYTHON" \
        --config "$config" --model "$model" --runtime-root "$root/execution" \
        --state "$root/state.json" --report "$root/report.json" \
        --network-mode online --preflight-report "$RUNNER/reports/lite30-preflight.json" \
        --asset-manifest-dir "$RUNNER/manifests/task-assets" --asset-source-root "$MLS" \
        --asset-receipt-root "$RUNNER/runtime/assets/receipts" --prepare-lock "$LOCK" \
        "${retry_args[@]}" --execute
    echo RUN_ROUND_ORCHESTRATION_OK
}

reconcile_state() {
    require_root "$1"; require_release "$2"
    local round="$3" execute="${4:-0}" root args=()
    [[ "$round" =~ ^[1-5]$ ]] || die "invalid round"
    [[ "$execute" =~ ^[01]$ ]] || die "execute flag must be 0 or 1"
    root="$(round_root "$round")"
    [[ "$execute" == 1 ]] && args+=(--execute)
    run_release_python -m mls_lite_runner reconcile-state --state "$root/state.json" "${args[@]}"
    echo RECONCILE_STATE_OK
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
        probe-shared-env) probe_shared_environment "$@" ;;
        prepare-release) prepare_release "$@" ;;
        upgrade-release) upgrade_release "$@" ;;
        host-smoke) host_smoke "$@" ;;
        api-smoke) api_smoke "$@" ;;
        run-task) run_task "$@" ;;
        run-round) run_round "$@" ;;
        reconcile-state) reconcile_state "$@" ;;
        report) report_round "$@" ;;
        *) die "usage: qz_entry.sh {probe-shared-env|prepare-release|upgrade-release|host-smoke|api-smoke|run-task|run-round|reconcile-state|report} ..." ;;
    esac
}

main "$@"
