param(
    [string]$ReleaseId = "v001",
    [string]$JobSuffix = "001",
    [ValidateRange(10, 14400)][int]$Minutes = 120,
    [string]$Root = "/inspire/hdd/project/long-working-agent/ky26299",
    [string]$ExpectedMlsCommit = "cfd57a7e0139c72753e32e31bca593719b098717",
    [string]$MiniSweVersion = "v2.4.6",
    [switch]$Execute
)

$ErrorActionPreference = "Stop"
if ($ReleaseId -notmatch '^[A-Za-z0-9._-]+$') { throw "Unsafe ReleaseId" }
if ($JobSuffix -notmatch '^[A-Za-z0-9._-]+$') { throw "Unsafe JobSuffix" }
if ($ExpectedMlsCommit -notmatch '^[0-9a-f]{40}$') { throw "ExpectedMlsCommit must be a full lowercase Git SHA" }
if ($MiniSweVersion -notmatch '^v[0-9]+\.[0-9]+\.[0-9]+$') { throw "MiniSweVersion must be a vX.Y.Z tag" }

$jobName = "mr3mh-install-env-$ReleaseId-$JobSuffix"
$template = @'
qz-job submit --profile cpu --cpu-spec 4c16g --name {0} --minutes {1} --command 'set -euo pipefail; ROOT="{2}"; MLS="$ROOT/code/MLS-Bench"; AGENT="$ROOT/code/mini-swe-agent-{4}"; ENV="$ROOT/runtime/envs/mlsbench-lite-agent-{5}"; CACHE="$ROOT/runtime/cache/pip"; mkdir -p "$ROOT/code" "$ROOT/runtime/envs" "$CACHE"; if [ -e "$MLS" ] && [ ! -d "$MLS/.git" ]; then echo "ERROR: MLS path exists but is not a Git repository"; exit 2; fi; if [ ! -d "$MLS/.git" ]; then git clone https://github.com/Imbernoulli/MLS-Bench.git "$MLS"; git -C "$MLS" checkout "{3}"; fi; test "$(git -C "$MLS" rev-parse HEAD)" = "{3}"; if [ -e "$AGENT" ] && [ ! -d "$AGENT/.git" ]; then echo "ERROR: Agent path exists but is not a Git repository"; exit 2; fi; if [ ! -d "$AGENT/.git" ]; then git clone --branch "{4}" --depth 1 https://github.com/SWE-agent/mini-swe-agent.git "$AGENT"; fi; git -C "$AGENT" describe --tags --exact-match | grep -Fx "{4}"; if [ ! -x "$ENV/bin/python" ]; then if command -v conda >/dev/null; then conda create -p "$ENV" python=3.11 pip -y; elif command -v python3.11 >/dev/null; then python3.11 -m venv "$ENV"; else echo "ERROR: need conda or python3.11 to create the shared environment"; exit 3; fi; fi; "$ENV/bin/python" -c "import sys; assert sys.version_info[:2] == (3, 11), sys.version"; PIP_CACHE_DIR="$CACHE" "$ENV/bin/python" -m pip install --upgrade pip setuptools wheel; PIP_CACHE_DIR="$CACHE" "$ENV/bin/python" -m pip install -e "${{MLS}}[agent]"; PIP_CACHE_DIR="$CACHE" "$ENV/bin/python" -m pip install -e "$AGENT" numpy; "$ENV/bin/python" -m pip check; "$ENV/bin/python" -c "import mlsbench, minisweagent, numpy, yaml; print('"'"'HOST_IMPORTS_OK'"'"')"; "$ENV/bin/python" -m pip show mlsbench mini-swe-agent | grep -E "^(Name|Version|Editable project location):"; echo INSTALL_ENV_OK'
'@
$remoteCommand = ($template -f $jobName, $Minutes, $Root, $ExpectedMlsCommit, $MiniSweVersion, $ReleaseId).Trim()
if (-not $Execute) { $remoteCommand; exit 0 }
ssh qz-gpu $remoteCommand
