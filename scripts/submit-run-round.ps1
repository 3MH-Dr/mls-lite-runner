param(
    [Parameter(Mandatory = $true)][ValidateRange(1, 5)][int]$Round,
    [Parameter(Mandatory = $true)][string]$Model,
    [string]$ReleaseId = "v001",
    [string]$JobSuffix = "001",
    [ValidateRange(1, 1440)][int]$Minutes = 1440,
    [string]$Root = "/inspire/hdd/project/long-working-agent/ky26299",
    [string]$SecretFile = "/inspire/hdd/project/long-working-agent/ky26299/runtime/secrets/deepseek.env",
    [string]$ApiKeyEnv = "DEEPSEEK_API_KEY",
    [switch]$RetryFailed,
    [switch]$Execute
)

$ErrorActionPreference = "Stop"
if ($ReleaseId -notmatch '^[A-Za-z0-9._-]+$') { throw "Unsafe ReleaseId" }
if ($JobSuffix -notmatch '^[A-Za-z0-9._-]+$') { throw "Unsafe JobSuffix" }
if ($Model -notmatch '^[A-Za-z0-9._:/-]+$') { throw "Unsafe model identifier" }
if ($ApiKeyEnv -notmatch '^[A-Z][A-Z0-9_]+$') { throw "Unsafe API key environment variable name" }
if ($SecretFile -notmatch '^/inspire/hdd/project/long-working-agent/ky26299/[A-Za-z0-9._/-]+$') { throw "SecretFile must stay under the project root" }

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$manifest = Get-Content -LiteralPath (Join-Path $projectRoot "manifests\lite30.json") -Raw | ConvertFrom-Json
$roundSpec = @($manifest.rounds | Where-Object { [int]$_.id -eq $Round })
if ($roundSpec.Count -ne 1) { throw "Round $Round is missing or duplicated in the manifest" }
$profile = [string]$roundSpec[0].platform_profile
$gpus = [int]$roundSpec[0].platform_gpus
if ($profile -ne "4090") { throw "This direct runner only accepts profile 4090, manifest has $profile" }
if ($gpus -notin @(1, 2, 4, 8)) { throw "Unsupported per-node GPU count: $gpus" }

$retry = if ($RetryFailed) { "--retry-failed" } else { "" }
$jobName = "mr3mh-mls-$ReleaseId-r$Round-$JobSuffix"
$template = @'
qz-job submit --profile {6} --gpus {5} --nodes 1 --name {7} --minutes {8} --command 'set -euo pipefail; ROOT="{0}"; RUNNER="$ROOT/code/mls-lite-runner-{1}"; MLS="$ROOT/code/MLS-Bench"; PYTHON="$ROOT/runtime/envs/mlsbench-lite-agent-{1}/bin/python"; CONFIG="$ROOT/runtime/configs/{1}/miniswe_bash.yaml"; STATE="$ROOT/runtime/state/{1}/lite30.json"; SECRET_FILE="{9}"; API_KEY_ENV="{10}"; test -d "$RUNNER"; test -d "$MLS"; test -x "$PYTHON"; test -f "$CONFIG"; test -r "$SECRET_FILE"; set -a; . "$SECRET_FILE"; set +a; test -n "$(printenv "$API_KEY_ENV")"; GPU_COUNT="$(nvidia-smi -L | wc -l | tr -d " ")"; GPU_DEVICES="$(nvidia-smi --query-gpu=index --format=csv,noheader | paste -sd, - | tr -d " ")"; echo "VISIBLE_GPUS=$GPU_COUNT EXPECTED_GPUS={5} CUDA_VISIBLE_DEVICES=$GPU_DEVICES"; test "$GPU_COUNT" -eq "{5}"; test "$(awk -F, "{{print NF}}" <<< "$GPU_DEVICES")" -eq "{5}"; export CUDA_VISIBLE_DEVICES="$GPU_DEVICES"; curl -sS -I --connect-timeout 10 --max-time 20 https://github.com/ >/dev/null; command -v docker >/dev/null; docker info >/dev/null; PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -m mls_lite_runner doctor --round {2} --mls-root "$MLS" --agent-root "$RUNNER" --python "$PYTHON"; PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -m mls_lite_runner run-round --round {2} --mls-root "$MLS" --python "$PYTHON" --config "$CONFIG" --model "{3}" --runtime-root "$ROOT/runtime/execution/{1}/round-{2}" --state "$STATE" --network-mode online --preflight-report "$RUNNER/reports/lite30-preflight.json" --asset-manifest-dir "$RUNNER/manifests/task-assets" --asset-source-root "$MLS" --asset-receipt-root "$ROOT/runtime/assets/receipts/{1}" {4} --execute; echo RUN_ROUND_OK'
'@
$remoteCommand = ($template -f $Root, $ReleaseId, $Round, $Model, $retry, $gpus, $profile, $jobName, $Minutes, $SecretFile, $ApiKeyEnv).Trim()
if (-not $Execute) { $remoteCommand; exit 0 }
ssh qz-gpu $remoteCommand
