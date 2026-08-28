param(
    [ValidateSet(1, 2, 4, 8)][int]$Gpus = 1,
    [string]$ReleaseId = "v003",
    [string]$JobSuffix = "001",
    [string]$Root = "/inspire/hdd/project/long-working-agent/ky26299",
    [switch]$Execute
)
$ErrorActionPreference = "Stop"
if ($ReleaseId -notmatch '^[A-Za-z0-9._-]+$' -or $JobSuffix -notmatch '^[A-Za-z0-9._-]+$') { throw "Unsafe identifier" }
$job = "mr3mh-4090-smoke-$ReleaseId-$Gpus-$JobSuffix"
$entry = "$Root/code/mls-lite-runner-$ReleaseId/platform/qz_entry.sh"
$inner = "bash $entry host-smoke $Root $ReleaseId $Gpus"
$command = "qz-job submit --profile 4090 --gpus $Gpus --nodes 1 --name $job --minutes 10 --command '$inner'"
if (-not $Execute) { $command; exit 0 }
ssh qz-gpu $command
