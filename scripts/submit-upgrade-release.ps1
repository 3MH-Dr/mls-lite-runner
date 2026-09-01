param(
    [string]$ReleaseId = "v003",
    [string]$JobSuffix = "001",
    [ValidateRange(10, 240)][int]$Minutes = 120,
    [string]$Root = "/inspire/hdd/project/long-working-agent/ky26299",
    [switch]$AllowEnvironmentChange,
    [switch]$Execute
)
$ErrorActionPreference = "Stop"
foreach ($value in @($ReleaseId, $JobSuffix)) {
    if ($value -notmatch '^[A-Za-z0-9._-]+$') { throw "Unsafe identifier: $value" }
}
$allowChange = if ($AllowEnvironmentChange) { 1 } else { 0 }
$job = "mr3mh-upgrade-$ReleaseId-$JobSuffix"
$entry = "$Root/code/mls-lite-runner-$ReleaseId/platform/qz_entry.sh"
$inner = "bash $entry upgrade-release $Root $ReleaseId $allowChange"
$command = "qz-job submit --profile 4090 --gpus 1 --nodes 1 --name $job --minutes $Minutes --command '$inner'"
if (-not $Execute) { $command; exit 0 }
ssh qz-gpu $command
