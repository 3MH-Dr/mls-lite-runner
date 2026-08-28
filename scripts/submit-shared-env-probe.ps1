param(
    [string]$JobSuffix = "001",
    [ValidateSet("cpu", "4090")][string]$Profile = "4090",
    [string]$Root = "/inspire/hdd/project/long-working-agent/ky26299",
    [switch]$Execute
)
$ErrorActionPreference = "Stop"
if ($JobSuffix -notmatch '^[A-Za-z0-9._-]+$') { throw "Unsafe JobSuffix" }
$job = "mr3mh-shared-env-probe-$Profile-$JobSuffix"
$entry = "$Root/code/qz_entry.sh"
$inner = "bash $entry probe-shared-env $Root"
$resources = if ($Profile -eq "cpu") { "--profile cpu --cpu-spec 4c16g" } else { "--profile 4090 --gpus 1 --nodes 1" }
$command = "qz-job submit $resources --name $job --minutes 10 --command '$inner'"
if (-not $Execute) { $command; exit 0 }
ssh qz-gpu $command
