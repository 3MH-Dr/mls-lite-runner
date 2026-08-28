param(
    [Parameter(Mandatory = $true)][string]$GitHubUrl,
    [string]$GitRef = "mls-lite-v003",
    [string]$ReleaseId = "v003",
    [string]$ExpectedMlsCommit = "cfd57a7e0139c72753e32e31bca593719b098717",
    [string]$MiniSweVersion = "v2.4.6",
    [switch]$AllowEnvironmentChange,
    [ValidateSet("cpu", "4090")][string]$Profile = "4090",
    [string]$JobSuffix = "001",
    [ValidateRange(10, 1440)][int]$Minutes = 120,
    [string]$Root = "/inspire/hdd/project/long-working-agent/ky26299",
    [switch]$Execute
)
$ErrorActionPreference = "Stop"
if ($GitHubUrl -notmatch '^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$') { throw "Invalid GitHub URL" }
foreach ($value in @($GitRef, $ReleaseId, $JobSuffix)) {
    if ($value -notmatch '^[A-Za-z0-9._/-]+$') { throw "Unsafe identifier: $value" }
}
if ($ExpectedMlsCommit -notmatch '^[0-9a-f]{40}$') { throw "ExpectedMlsCommit must be a full SHA" }
if ($MiniSweVersion -ne 'v2.4.6') { throw "This release entry currently pins mini-SWE v2.4.6" }
$job = "mr3mh-prepare-$ReleaseId-$Profile-$JobSuffix"
$entry = "$Root/code/qz_entry.sh"
$allowChange = if ($AllowEnvironmentChange) { 1 } else { 0 }
$inner = "bash $entry prepare-release $Root $GitHubUrl $GitRef $ReleaseId $ExpectedMlsCommit $MiniSweVersion $allowChange"
$resources = if ($Profile -eq "cpu") { "--profile cpu --cpu-spec 4c16g" } else { "--profile 4090 --gpus 1 --nodes 1" }
$command = "qz-job submit $resources --name $job --minutes $Minutes --command '$inner'"
if (-not $Execute) { $command; exit 0 }
ssh qz-gpu $command
