param(
    [Parameter(Mandatory = $true)][ValidateRange(1, 5)][int]$Round,
    [switch]$Execute
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$manifest = Get-Content -LiteralPath (Join-Path $projectRoot "manifests\lite30.json") -Raw | ConvertFrom-Json
$roundSpec = @($manifest.rounds | Where-Object { [int]$_.id -eq $Round })
if ($roundSpec.Count -ne 1) { throw "Round $Round is missing or duplicated" }
$profile = [string]$roundSpec[0].platform_profile
$gpus = [int]$roundSpec[0].platform_gpus
$remoteCommand = "qz-job template-info --profile $profile --gpus $gpus --nodes 1"
if (-not $Execute) { $remoteCommand; exit 0 }
ssh qz-gpu $remoteCommand
