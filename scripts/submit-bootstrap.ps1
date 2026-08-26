param(
    [Parameter(Mandatory = $true)][string]$GitHubUrl,
    [string]$Ref = "main",
    [string]$ExpectedMlsCommit = "cfd57a7e0139c72753e32e31bca593719b098717",
    [string]$ReleaseId = "v001",
    [string]$JobSuffix = "001",
    [string]$Root = "/inspire/hdd/project/long-working-agent/ky26299",
    [switch]$Execute
)

$ErrorActionPreference = "Stop"
if ($ReleaseId -notmatch '^[A-Za-z0-9._-]+$') { throw "Unsafe ReleaseId" }
if ($JobSuffix -notmatch '^[A-Za-z0-9._-]+$') { throw "Unsafe JobSuffix" }
if ($Ref -notmatch '^[A-Za-z0-9._/-]+$') { throw "Unsafe Ref" }
if ($GitHubUrl -notmatch '^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$') { throw "Use a normal HTTPS GitHub repository URL" }
if ($ExpectedMlsCommit -notmatch '^[0-9a-f]{40}$') { throw "ExpectedMlsCommit must be a full lowercase Git SHA" }

$jobName = "mls-lite-bootstrap-$ReleaseId-$JobSuffix"
$template = @'
qz-job submit --profile cpu --cpu-spec 4c16g --name {5} --minutes 120 --command 'set -euo pipefail; ROOT="{0}"; RUNNER="$ROOT/code/mls-lite-runner-{2}"; MLS="$ROOT/code/MLS-Bench"; PYTHON="$ROOT/runtime/envs/mlsbench-lite-agent-{2}/bin/python"; RECORDS="$ROOT/runtime/records/{2}"; test -x "$PYTHON"; test -d "$MLS/.git"; test "$(git -C "$MLS" rev-parse HEAD)" = "{4}"; "$PYTHON" -c "import minisweagent; print(minisweagent.__version__)"; test ! -e "$RUNNER"; git clone --branch "{1}" --depth 1 "{3}" "$RUNNER"; mkdir -p "$RECORDS" "$ROOT/runtime/configs/{2}" "$ROOT/runtime/runs/{2}" "$ROOT/runtime/state/{2}" "$ROOT/runtime/assets/receipts/{2}"; git -C "$MLS" status --short > "$RECORDS/mls-status-before.txt"; git -C "$MLS" diff --binary > "$RECORDS/mls-diff-before.patch"; if grep -Fq "from mls_agent.miniswe_bash_agent import MiniSWEBashAgent" "$MLS/src/mlsbench/cli.py"; then PATCH=""; elif grep -Fq "from mlsbench.agent.miniswe_bash_agent import MiniSWEBashAgent" "$MLS/src/mlsbench/cli.py"; then PATCH="$RUNNER/patches/mls-registration-upgrade-v1.patch"; else PATCH="$RUNNER/patches/mls-registration-clean.patch"; fi; if [ -n "$PATCH" ]; then sha256sum "$PATCH" > "$RECORDS/registration-patch.sha256"; git -C "$MLS" apply --check "$PATCH"; git -C "$MLS" apply "$PATCH"; fi; "$PYTHON" -m pip install -e "$RUNNER"; PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -m mlsbench.cli agent --help | grep -F "miniswe-bash" >/dev/null; PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -m mls_lite_runner validate; PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -m mls_lite_runner write-config --output "$ROOT/runtime/configs/{2}/miniswe_bash.yaml" --save-path "$ROOT/runtime/runs/{2}"; PYTHONPATH="$RUNNER/src:$MLS/src" "$PYTHON" -m mls_lite_runner init-state --state "$ROOT/runtime/state/{2}/lite30.json" >/dev/null; echo BOOTSTRAP_OK'
'@
$remoteCommand = ($template -f $Root, $Ref, $ReleaseId, $GitHubUrl, $ExpectedMlsCommit, $jobName).Trim()
if (-not $Execute) { $remoteCommand; exit 0 }
ssh qz-gpu $remoteCommand
