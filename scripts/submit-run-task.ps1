param(
    [Parameter(Mandatory = $true)][string]$Task,
    [Parameter(Mandatory = $true)][string]$ApiKey,
    [string]$Model = "deepseekflash",
    [string]$ApiKeyEnv = "DEEPSEEK_API_KEY",
    [string]$ReleaseId = "v003",
    [string]$JobSuffix = "001",
    [ValidateRange(1, 1440)][int]$Minutes = 360,
    [string]$Root = "/inspire/hdd/project/long-working-agent/ky26299",
    [switch]$Execute
)
$ErrorActionPreference = "Stop"
if ($ApiKey -notmatch '^[A-Za-z0-9._-]+$' -or $Task -notmatch '^[a-z0-9-]+$') { throw "Unsafe task or API key" }
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$manifest = Get-Content -LiteralPath (Join-Path $projectRoot "manifests\lite30.json") -Raw | ConvertFrom-Json
$matches = @($manifest.rounds | Where-Object { $Task -in @($_.tasks | ForEach-Object { $_.id }) })
if ($matches.Count -ne 1) { throw "Task is missing or duplicated" }
$round = [int]$matches[0].id; $gpus = [int]$matches[0].platform_gpus
$job = "mr3mh-task-$ReleaseId-$Task-$JobSuffix"
$entry = "$Root/code/mls-lite-runner-$ReleaseId/platform/qz_entry.sh"
$inner = "bash $entry run-task $Root $ReleaseId $Task $Model $round $gpus $ApiKeyEnv $ApiKey"
$command = "qz-job submit --profile 4090 --docker --gpus $gpus --nodes 1 --name $job --minutes $Minutes --command '$inner'"
if (-not $Execute) { $command; exit 0 }
ssh qz-gpu $command
