param(
    [Parameter(Mandatory = $true)][string]$Model,
    [string]$ReleaseId = "v001",
    [string]$JobSuffix = "001",
    [string]$Root = "/inspire/hdd/project/long-working-agent/ky26299",
    [string]$SecretFile = "/inspire/hdd/project/long-working-agent/ky26299/runtime/secrets/deepseek.env",
    [string]$ApiKeyEnv = "DEEPSEEK_API_KEY",
    [switch]$Execute
)

$ErrorActionPreference = "Stop"
if ($ReleaseId -notmatch '^[A-Za-z0-9._-]+$') { throw "Unsafe ReleaseId" }
if ($JobSuffix -notmatch '^[A-Za-z0-9._-]+$') { throw "Unsafe JobSuffix" }
if ($Model -notmatch '^[A-Za-z0-9._:/-]+$') { throw "Unsafe model identifier" }
if ($ApiKeyEnv -notmatch '^[A-Z][A-Z0-9_]+$') { throw "Unsafe API key environment variable name" }
if ($SecretFile -notmatch '^/inspire/hdd/project/long-working-agent/ky26299/[A-Za-z0-9._/-]+$') { throw "SecretFile must stay under the project root" }

$jobName = "mr3mh-api-smoke-$ReleaseId-$JobSuffix"
$template = @'
qz-job submit --profile 4090 --gpus 1 --nodes 1 --name {0} --minutes 10 --command 'set -euo pipefail; ROOT="{1}"; RUNNER="$ROOT/code/mls-lite-runner-{2}"; MLS="$ROOT/code/MLS-Bench"; PYTHON="$ROOT/runtime/envs/mlsbench-lite-agent-{2}/bin/python"; CONFIG="$ROOT/runtime/configs/{2}/miniswe_bash.yaml"; SECRET_FILE="{4}"; API_KEY_ENV="{5}"; test -r "$SECRET_FILE"; set -a; . "$SECRET_FILE"; set +a; test -n "$(printenv "$API_KEY_ENV")"; nvidia-smi -L; curl -sS -I --connect-timeout 10 --max-time 20 https://github.com/ >/dev/null; PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -m mls_lite_runner api-smoke --config "$CONFIG" --model "{3}"; echo API_JOB_OK'
'@
$remoteCommand = ($template -f $jobName, $Root, $ReleaseId, $Model, $SecretFile, $ApiKeyEnv).Trim()
if (-not $Execute) { $remoteCommand; exit 0 }
ssh qz-gpu $remoteCommand
