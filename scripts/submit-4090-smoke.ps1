param(
    [ValidateSet(1, 2, 4, 8)][int]$Gpus = 2,
    [string]$ReleaseId = "v001",
    [string]$JobSuffix = "001",
    [string]$Root = "/inspire/hdd/project/long-working-agent/ky26299",
    [switch]$Execute
)

$ErrorActionPreference = "Stop"
if ($ReleaseId -notmatch '^[A-Za-z0-9._-]+$') { throw "Unsafe ReleaseId" }
if ($JobSuffix -notmatch '^[A-Za-z0-9._-]+$') { throw "Unsafe JobSuffix" }
$jobName = "mr3mh-4090-smoke-$Gpus-$JobSuffix"
$template = @'
qz-job submit --profile 4090 --gpus {0} --nodes 1 --name {1} --minutes 10 --command 'set -euo pipefail; ROOT="{2}"; RUNNER="$ROOT/code/mls-lite-runner-{3}"; MLS="$ROOT/code/MLS-Bench"; PYTHON="$ROOT/runtime/envs/mlsbench-lite-agent-{3}/bin/python"; GPU_COUNT="$(nvidia-smi -L | tee /dev/stderr | wc -l | tr -d " ")"; GPU_DEVICES="$(nvidia-smi --query-gpu=index --format=csv,noheader | paste -sd, - | tr -d " ")"; echo "VISIBLE_GPUS=$GPU_COUNT EXPECTED_GPUS={0} CUDA_VISIBLE_DEVICES=$GPU_DEVICES"; test "$GPU_COUNT" -eq "{0}"; test "$(awk -F, "{{print NF}}" <<< "$GPU_DEVICES")" -eq "{0}"; export CUDA_VISIBLE_DEVICES="$GPU_DEVICES"; curl -sS -I --connect-timeout 10 --max-time 20 https://github.com/ >/dev/null; command -v docker >/dev/null; docker info >/dev/null; test -d "$RUNNER"; test -d "$MLS"; PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -c "import mlsbench, mls_agent, minisweagent, numpy; print(\"PYTHON_IMPORTS_OK\")"; echo SMOKE_4090_OK'
'@
$remoteCommand = ($template -f $Gpus, $jobName, $Root, $ReleaseId).Trim()
if (-not $Execute) { $remoteCommand; exit 0 }
ssh qz-gpu $remoteCommand
