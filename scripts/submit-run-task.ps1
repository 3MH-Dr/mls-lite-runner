param(
    [Parameter(Mandatory = $true)][string]$Task,
    [Parameter(Mandatory = $true)][string]$Model,
    [string]$ReleaseId = "v001",
    [string]$JobSuffix = "001",
    [ValidateRange(1, 1440)][int]$Minutes = 360,
    [string]$Root = "/inspire/hdd/project/long-working-agent/ky26299",
    [string]$SecretFile = "/inspire/hdd/project/long-working-agent/ky26299/runtime/secrets/deepseek.env",
    [string]$ApiKeyEnv = "DEEPSEEK_API_KEY",
    [switch]$Execute
)

$ErrorActionPreference = "Stop"
if ($Task -notmatch '^[a-z0-9-]+$') { throw "Unsafe task id" }
if ($ReleaseId -notmatch '^[A-Za-z0-9._-]+$') { throw "Unsafe ReleaseId" }
if ($JobSuffix -notmatch '^[A-Za-z0-9._-]+$') { throw "Unsafe JobSuffix" }
if ($Model -notmatch '^[A-Za-z0-9._:/-]+$') { throw "Unsafe model identifier" }
if ($ApiKeyEnv -notmatch '^[A-Z][A-Z0-9_]+$') { throw "Unsafe API key environment variable name" }
if ($SecretFile -notmatch '^/inspire/hdd/project/long-working-agent/ky26299/[A-Za-z0-9._/-]+$') { throw "SecretFile must stay under the project root" }

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$manifest = Get-Content -LiteralPath (Join-Path $projectRoot "manifests\lite30.json") -Raw | ConvertFrom-Json
$matches = @()
foreach ($round in $manifest.rounds) {
    if (@($round.tasks | Where-Object { $_.id -eq $Task }).Count -eq 1) {
        $matches += [pscustomobject]@{ Round = [int]$round.id; Profile = [string]$round.platform_profile; Gpus = [int]$round.platform_gpus }
    }
}
if ($matches.Count -ne 1) { throw "Task $Task is missing or duplicated in the Lite manifest" }
$roundId = $matches[0].Round
$profile = $matches[0].Profile
$gpus = $matches[0].Gpus
if ($profile -ne "4090" -or $gpus -notin @(1, 2, 4, 8)) { throw "Unsupported task platform allocation" }

$jobName = "mr3mh-task-$ReleaseId-$Task-$JobSuffix"
$template = @'
qz-job submit --profile {6} --gpus {5} --nodes 1 --name {7} --minutes {8} --command 'set -euo pipefail; ROOT="{0}"; RUNNER="$ROOT/code/mls-lite-runner-{1}"; MLS="$ROOT/code/MLS-Bench"; PYTHON="$ROOT/runtime/envs/mlsbench-lite-agent-{1}/bin/python"; CONFIG="$ROOT/runtime/configs/{1}/miniswe_bash.yaml"; STATE="$ROOT/runtime/state/{1}/lite30.json"; SECRET_FILE="{9}"; API_KEY_ENV="{10}"; test -r "$SECRET_FILE"; set -a; . "$SECRET_FILE"; set +a; test -n "$(printenv "$API_KEY_ENV")"; GPU_COUNT="$(nvidia-smi -L | wc -l | tr -d " ")"; GPU_DEVICES="$(nvidia-smi --query-gpu=index --format=csv,noheader | paste -sd, - | tr -d " ")"; test "$GPU_COUNT" -eq "{5}"; export CUDA_VISIBLE_DEVICES="$GPU_DEVICES"; command -v docker >/dev/null; docker info >/dev/null; PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -m mls_lite_runner doctor --round {4} --mls-root "$MLS" --agent-root "$RUNNER" --python "$PYTHON"; PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -m mls_lite_runner run-task "{2}" --mls-root "$MLS" --python "$PYTHON" --config "$CONFIG" --model "{3}" --runtime-root "$ROOT/runtime/execution/{1}/round-{4}" --state "$STATE" --network-mode online --preflight-report "$RUNNER/reports/lite30-preflight.json" --asset-manifest-dir "$RUNNER/manifests/task-assets" --asset-source-root "$MLS" --asset-receipt-root "$ROOT/runtime/assets/receipts/{1}" --execute; echo RUN_TASK_OK'
'@
$remoteCommand = ($template -f $Root, $ReleaseId, $Task, $Model, $roundId, $gpus, $profile, $jobName, $Minutes, $SecretFile, $ApiKeyEnv).Trim()
if (-not $Execute) { $remoteCommand; exit 0 }
ssh qz-gpu $remoteCommand
