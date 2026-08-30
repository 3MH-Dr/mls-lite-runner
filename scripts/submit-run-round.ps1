param(
    [Parameter(Mandatory = $true)][ValidateRange(1, 5)][int]$Round,
    [Parameter(Mandatory = $true)][string[]]$Tasks,
    [Parameter(Mandatory = $true)][string]$ApiKey,
    [string]$Model = "deepseekflash",
    [string]$ApiKeyEnv = "DEEPSEEK_API_KEY",
    [string]$ReleaseId = "v003",
    [string]$JobSuffix = "001",
    [ValidateRange(1, 1440)][int]$Minutes = 1440,
    [string]$Root = "/inspire/hdd/project/long-working-agent/ky26299",
    [switch]$RetryFailed,
    [switch]$Execute
)
$ErrorActionPreference = "Stop"
if ($ApiKey -notmatch '^[A-Za-z0-9._-]+$') { throw "API key contains characters unsafe for direct command transport" }
if ($Model -notmatch '^[A-Za-z0-9._:/-]+$' -or $ApiKeyEnv -notmatch '^[A-Z][A-Z0-9_]+$') { throw "Unsafe model or API variable" }
if ($Tasks.Count -lt 1 -or $Tasks.Count -gt 6 -or @($Tasks | Select-Object -Unique).Count -ne $Tasks.Count) { throw "Tasks must contain 1-6 unique ids" }
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$manifest = Get-Content -LiteralPath (Join-Path $projectRoot "manifests\lite30.json") -Raw | ConvertFrom-Json
$spec = @($manifest.rounds | Where-Object { [int]$_.id -eq $Round })[0]
$allowed = @($spec.tasks | ForEach-Object { [string]$_.id })
foreach ($task in $Tasks) { if ($task -notin $allowed) { throw "$task is not in round $Round" } }
$gpus = [int]$spec.platform_gpus
$taskCsv = $Tasks -join ','
$retry = if ($RetryFailed) { 1 } else { 0 }
$job = "mr3mh-mls-$ReleaseId-r$Round-$JobSuffix"
$entry = "$Root/code/mls-lite-runner-$ReleaseId/platform/qz_entry.sh"
$inner = "bash $entry run-round $Root $ReleaseId $Round $Model $gpus $ApiKeyEnv $ApiKey $taskCsv $retry"
$command = "qz-job submit --profile 4090 --docker --gpus $gpus --nodes 1 --name $job --minutes $Minutes --command '$inner'"
if (-not $Execute) { $command; exit 0 }
ssh qz-gpu $command
