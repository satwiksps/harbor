# Command doubles only. Never contacts Docker or Kubernetes.
. "$PSScriptRoot/common.ps1"
$script:ClusterName = 'harbor'
$script:KubeContext = 'k3d-harbor'
$script:Namespace = 'harbor'
. "$PSScriptRoot/local-bindings.ps1"
$testRoot = Join-Path ([IO.Path]::GetTempPath()) "harbor-bindings-$([guid]::NewGuid())"
New-Item -ItemType Directory -Path $testRoot | Out-Null
$script:RuntimeRoot = $testRoot

function New-TestBindings([string]$Address = '') {
    return @{
        '80/tcp' = @(@{ HostIp = $Address; HostPort = '8080' })
        '443/tcp' = @(@{ HostIp = $Address; HostPort = '8443' })
        '6443/tcp' = @(@{ HostIp = $Address; HostPort = '49700' })
    }
}
function Read-LocalBindingState { return @{ Bindings = $script:Ports; Server = $script:Fingerprint } }
function Test-LocalApi { return $true }
function Invoke-Native {
    param([string]$Command, [Parameter(ValueFromRemainingArguments)][string[]]$Arguments)
    if ($Command -eq 'kubectl') {
        if (($Arguments -join '|') -ne 'config|set-cluster|k3d-harbor|--server=https://127.0.0.1:49700') {
            throw 'Unexpected kubeconfig change'
        }
        $script:ConfigUpdates++
        return
    }
    if ($Command -ne $script:K3d -or ($Arguments[0..2] -join '|') -ne 'node|edit|k3d-harbor-serverlb') {
        throw 'Only the project load balancer may be replaced'
    }
    $script:Edits++
    $operation = $Arguments[3]
    if ($operation -eq '--port-add' -and $script:FailAdd) { throw 'Simulated interruption before adding ports' }
    for ($index = 3; $index -lt $Arguments.Count; $index += 2) {
        if ($Arguments[$index] -ne $operation) { throw 'Do not mix port addition and deletion' }
        $parts = $Arguments[$index + 1].Split(':')
        if ($operation -eq '--port-delete') {
            if ($parts.Count -ne 2 -or $parts[1] -notin @('80/tcp', '443/tcp', '6443/tcp')) { throw 'Unexpected deletion' }
            if ($script:Ports[$parts[1]][0].HostPort -ne $parts[0]) { throw 'Wrong original port removed' }
            $script:Ports.Remove($parts[1])
        } elseif ($operation -eq '--port-add') {
            if ($parts.Count -ne 3 -or $parts[0] -ne '127.0.0.1') { throw 'New bindings must be explicit loopback' }
            $script:Ports[$parts[2]] = @(@{ HostIp = $parts[0]; HostPort = $parts[1] })
        } else { throw 'Unexpected mutation' }
    }
}

try {
    foreach ($case in @('preview', 'already-local', 'migration', 'interrupted-migration', 'custom-port')) {
        $planFile = Join-Path $testRoot 'local-binding-migration.json'
        if (Test-Path -LiteralPath $planFile) { Remove-Item -LiteralPath $planFile }
        $script:Ports = New-TestBindings
        $script:Fingerprint = 'same-server|same-start-time|same-volume-mounts'
        $script:FailAdd = $false
        $script:Edits = 0
        $script:ConfigUpdates = 0
        if ($case -eq 'already-local') { $script:Ports = New-TestBindings '127.0.0.1' }
        if ($case -eq 'custom-port') { $script:Ports['80/tcp'][0].HostPort = '8081' }
        if ($case -eq 'interrupted-migration') { $script:FailAdd = $true }
        $failed = $false
        try { Set-LocalLoopbackBindings -Apply:($case -ne 'preview') } catch { $failed = $true }
        if ($failed -ne ($case -in @('custom-port', 'interrupted-migration'))) { throw "Unexpected outcome: $case" }
        if ($case -eq 'interrupted-migration') {
            if ($script:Ports.Count -ne 0 -or $script:Edits -ne 2) { throw 'Unexpected interruption state' }
            $script:FailAdd = $false
            Set-LocalLoopbackBindings -Apply
            if ($script:Edits -ne 3) { throw 'Resume must only add the missing loopback bindings' }
        }
        if ($case -eq 'migration' -and $script:Edits -ne 2) { throw 'Migration must use two scoped replacements' }
        if ($case -in @('already-local', 'preview', 'custom-port') -and $script:Edits -ne 0) { throw 'Unnecessary mutation' }
        if ($case -notin @('preview', 'custom-port')) {
            $verified = Get-LocalLoopbackPlan $script:Ports
            if ($verified.ApiPort -ne 49700 -or $verified.Add.Count -or $verified.Remove.Count -or $script:ConfigUpdates -ne 1) { throw 'Ports or kubeconfig were not preserved' }
        }
        Write-Host "PASS: bindings-$case"
    }
    $script:Ports = New-TestBindings
    $script:Ports['9000/tcp'] = @(@{ HostIp = ''; HostPort = '9000' })
    $failed = $false
    try { Get-LocalLoopbackPlan $script:Ports | Out-Null } catch { $failed = $true }
    if (-not $failed) { throw 'Unexpected ports must stop migration' }
    Write-Host 'PASS: bindings-reject-extra-ports'

    $apiPort = Get-AvailableLoopbackPort
    if ($apiPort -lt 1 -or $apiPort -gt 65535) { throw 'API port must be resolved before cluster creation' }
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $apiPort)
    try { $listener.Start() } finally { $listener.Stop() }
    Write-Host 'PASS: fresh-api-port-is-valid-and-released'

    # Parse the real fresh-install command and capture its expanded arguments.
    $ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'new-local-cluster.ps1'), [ref]$null, [ref]$null)
    $creation = @($ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.CommandAst] -and $node.Extent.Text -like 'Invoke-Native * cluster create *' }, $true))
    if ($creation.Count -ne 1) { throw 'Expected one cluster creation command' }
    function Invoke-Native {
        param([string]$Command, [Parameter(ValueFromRemainingArguments)][string[]]$Arguments)
        $script:CreateArguments = $Arguments
    }
    & ([scriptblock]::Create($creation[0].Extent.Text))
    foreach ($mapping in @('127.0.0.1:8080:80@loadbalancer', '127.0.0.1:8443:443@loadbalancer', "127.0.0.1:$apiPort")) {
        if ($script:CreateArguments -notcontains $mapping) { throw "Missing loopback mapping: $mapping" }
    }
    if ($script:CreateArguments -contains '127.0.0.1:0') { throw 'A literal API port zero breaks kubeconfig' }
    Write-Host 'PASS: fresh-cluster-loopback-bindings'
} finally {
    $planFile = Join-Path $testRoot 'local-binding-migration.json'
    if (Test-Path -LiteralPath $planFile) { Remove-Item -LiteralPath $planFile }
    Remove-Item -LiteralPath $testRoot
}
$global:LASTEXITCODE = 0
