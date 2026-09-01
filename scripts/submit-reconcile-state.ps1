param(
    [Parameter(Mandatory = $true)][ValidateRange(1, 5)][int]$Round,
    [string]$ReleaseId = "v003",
    [string]$JobSuffix = "001",
    [ValidateRange(5, 60)][int]$Minutes = 15,
    [string]$Root = "/inspire/hdd/project/long-working-agent/ky26299",
    [switch]$Apply,
    [switch]$Execute
)
$ErrorActionPreference = "Stop"
foreach ($value in @($ReleaseId, $JobSuffix)) {
    if ($value -notmatch '^[A-Za-z0-9._-]+$') { throw "Unsafe identifier: $value" }
}
$applyFlag = if ($Apply) { 1 } else { 0 }
$job = "mr3mh-reconcile-$ReleaseId-r$Round-$JobSuffix"
$entry = "$Root/code/mls-lite-runner-$ReleaseId/platform/qz_entry.sh"
$inner = "bash $entry reconcile-state $Root $ReleaseId $Round $applyFlag"
$command = "qz-job submit --profile 4090 --gpus 1 --nodes 1 --name $job --minutes $Minutes --command '$inner'"
if (-not $Execute) { $command; exit 0 }
ssh qz-gpu $command
