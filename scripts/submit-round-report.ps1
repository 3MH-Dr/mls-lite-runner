param(
    [Parameter(Mandatory = $true)][ValidateRange(1, 5)][int]$Round,
    [string]$ReleaseId = "v003",
    [string]$JobSuffix = "001",
    [string]$Root = "/inspire/hdd/project/long-working-agent/ky26299",
    [switch]$Execute
)
$job = "mr3mh-report-$ReleaseId-r$Round-$JobSuffix"
$entry = "$Root/code/mls-lite-runner-$ReleaseId/platform/qz_entry.sh"
$inner = "bash $entry report $Root $ReleaseId $Round"
$command = "qz-job submit --profile cpu --cpu-spec 1c4g --name $job --minutes 10 --command '$inner'"
if (-not $Execute) { $command; exit 0 }
ssh qz-gpu $command
