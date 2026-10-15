# Isolated command-double regression. This script never contacts Docker or Kubernetes.
. "$PSScriptRoot/common.ps1"
function kubectl {
    if ($args -notcontains '--context' -or $args -notcontains 'k3d-urumi-commerce') { throw 'Unexpected Kubernetes context' }
    if ($script:CheckingInstallation) {
        $global:LASTEXITCODE = $script:CheckExitCode
        return $script:ExistingResources
    }
    $script:HostChecks++
    $global:LASTEXITCODE = if ($script:HostReady) { 0 } else { 1 }
}
$script:CheckingInstallation = $false
function docker {
    if ($args[0] -eq 'exec') {
        if ($args[1] -ne 'k3d-urumi-commerce-server-0') { throw 'Unexpected node target' }
        $global:LASTEXITCODE = if ($script:NodeReady) { 0 } else { 1 }
    } elseif ($args[0] -eq 'restart') {
        if ($args.Count -ne 2 -or $args[1] -ne 'k3d-urumi-commerce-serverlb') { throw 'Unexpected restart target' }
        $script:Restarts++
        $script:HostReady = $script:ProxyRecovers
        $global:LASTEXITCODE = if ($script:RestartSucceeds) { 0 } else { 1 }
    } else { throw 'Unexpected Docker operation' }
}
function Start-Sleep { param([int]$Seconds) }
foreach ($case in @('healthy', 'stale-proxy', 'node-unavailable', 'proxy-restart-failed', 'proxy-still-unavailable')) {
    $script:HostChecks = 0
    $script:Restarts = 0
    $script:HostReady = $case -eq 'healthy'
    $script:NodeReady = $case -ne 'node-unavailable'
    $script:RestartSucceeds = $case -ne 'proxy-restart-failed'
    $script:ProxyRecovers = $case -ne 'proxy-still-unavailable'
    $failed = $false
    try { Repair-LocalApiProxy } catch { $failed = $true }
    if ($case -eq 'healthy' -and ($failed -or $script:Restarts -ne 0 -or $script:HostChecks -ne 1)) { throw 'Healthy API must remain untouched' }
    if ($case -eq 'stale-proxy' -and ($failed -or $script:Restarts -ne 1 -or $script:HostChecks -ne 7)) { throw 'Stale proxy must recover with one scoped restart' }
    if ($case -eq 'node-unavailable' -and (-not $failed -or $script:Restarts -ne 0 -or $script:HostChecks -ne 6)) { throw 'An unhealthy node must fail without restarting the proxy' }
    if ($case -eq 'proxy-restart-failed' -and (-not $failed -or $script:Restarts -ne 1 -or $script:HostChecks -ne 6)) { throw 'A failed proxy restart must stop after one scoped attempt' }
    if ($case -eq 'proxy-still-unavailable' -and (-not $failed -or $script:Restarts -ne 1 -or $script:HostChecks -ne 12)) { throw 'An unrecovered proxy must fail after bounded health checks' }
    Write-Host "PASS: $case"
}
$script:CheckingInstallation = $true
foreach ($case in @('new-install', 'retained-data', 'api-error')) {
    $script:CheckExitCode = if ($case -eq 'api-error') { 1 } else { 0 }
    $script:ExistingResources = if ($case -eq 'retained-data') { 'persistentvolumeclaim/wordpress-data' } else { $null }
    $failed = $false
    try { Assert-NoExistingInstallation } catch { $failed = $true }
    if ($failed -ne ($case -ne 'new-install')) { throw "Unexpected credential-generation decision: $case" }
    Write-Host "PASS: $case"
}
# Expected mocked failures must not become the GitHub PowerShell step's exit code.
$global:LASTEXITCODE = 0
& "$PSScriptRoot/test-local-bindings.ps1"
