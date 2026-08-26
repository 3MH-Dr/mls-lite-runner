param(
    [Parameter(Mandatory = $true)][string]$ApiKey,
    [string]$Model = "deepseekflash",
    [string]$ApiKeyEnv = "DEEPSEEK_API_KEY",
    [string]$ReleaseId = "v002",
    [string]$JobSuffix = "001",
    [string]$Root = "/inspire/hdd/project/long-working-agent/ky26299",
    [switch]$Execute
)
$ErrorActionPreference = "Stop"
if ($ApiKey -notmatch '^[A-Za-z0-9._-]+$') { throw "API key contains characters unsafe for direct command transport" }
if ($Model -notmatch '^[A-Za-z0-9._:/-]+$' -or $ApiKeyEnv -notmatch '^[A-Z][A-Z0-9_]+$') { throw "Unsafe model or API variable" }
$job = "mr3mh-api-smoke-$ReleaseId-$JobSuffix"
$entry = "$Root/code/mls-lite-runner-$ReleaseId/platform/qz_entry.sh"
$inner = "bash $entry api-smoke $Root $ReleaseId $Model $ApiKeyEnv $ApiKey"
$command = "qz-job submit --profile 4090 --gpus 1 --nodes 1 --name $job --minutes 10 --command '$inner'"
if (-not $Execute) { $command; exit 0 }
ssh qz-gpu $command
