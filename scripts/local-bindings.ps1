# Helpers used by the explicit maintenance command and its isolated tests.
function Get-AvailableLoopbackPort {
    # k3d stores a literal port 0 in kubeconfig, so resolve an ephemeral port first.
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    try {
        $listener.Start()
        return $listener.LocalEndpoint.Port
    } finally {
        $listener.Stop()
    }
}

function Get-LocalLoopbackPlan {
    param([hashtable]$Bindings, [int]$RecoveryApiPort = 0)
    $expected = @('80/tcp', '443/tcp', '6443/tcp')
    if (@($Bindings.Keys | Where-Object { $_ -notin $expected }).Count) {
        throw 'Unexpected load-balancer port. Refusing to alter a customized cluster.'
    }
    $apiPort = $RecoveryApiPort
    $remove = @()
    $add = @()
    foreach ($port in $expected) {
        $rows = @($Bindings[$port]) | Where-Object { $null -ne $_ }
        if (@($rows).Count -gt 1) { throw "Multiple bindings for $port require manual review" }
        $row = @($rows)[0]
        $hostPort = switch ($port) { '80/tcp' { 8080 } '443/tcp' { 8443 } '6443/tcp' { $apiPort } }
        if ($row) {
            if ([string]$row.HostPort -notmatch '^\d{1,5}$' -or [int]$row.HostPort -lt 1 -or [int]$row.HostPort -gt 65535) {
                throw "Invalid host port for $port"
            }
            if ($port -eq '6443/tcp' -and -not $apiPort) { $hostPort = $apiPort = [int]$row.HostPort }
            if ([int]$row.HostPort -ne $hostPort) { throw "Unexpected host port for $port" }
            if ($row.HostIp -notin @('', '0.0.0.0', '127.0.0.1')) { throw "Unexpected host address for $port" }
        } elseif (-not $RecoveryApiPort) {
            throw 'Missing original bindings. A matching saved migration plan is required for recovery.'
        }
        if ($hostPort -lt 1 -or $hostPort -gt 65535) { throw 'A valid original API port is required' }
        if ($row -and $row.HostIp -eq '127.0.0.1') { continue }
        if ($row) { $remove += "${hostPort}:$port" }
        $add += "127.0.0.1:${hostPort}:$port"
    }
    return @{ ApiPort = $apiPort; Remove = $remove; Add = $add }
}

function Read-LocalBindingState {
    $loadBalancer = "k3d-$script:ClusterName-serverlb"
    $identity = & docker inspect $loadBalancer --format '{{index .Config.Labels "k3d.cluster"}}|{{index .Config.Labels "k3d.role"}}'
    if ($LASTEXITCODE -ne 0 -or $identity -ne "$script:ClusterName|loadbalancer") {
        throw 'The expected project load balancer was not found'
    }
    $ports = & docker inspect $loadBalancer --format '{{json .HostConfig.PortBindings}}'
    if ($LASTEXITCODE -ne 0) { throw 'Could not read project port bindings' }
    $server = & docker inspect "k3d-$script:ClusterName-server-0" --format '{{.Id}}|{{.State.StartedAt}}|{{json .Mounts}}'
    if ($LASTEXITCODE -ne 0 -or -not $server) { throw 'Could not identify the retained project server and volumes' }
    $bindings = $ports | ConvertFrom-Json -AsHashtable
    if ($null -eq $bindings) { $bindings = @{} }
    return @{ Bindings = $bindings; Server = $server }
}

function Set-LocalLoopbackBindings {
    param([switch]$Apply)
    $before = Read-LocalBindingState
    $planFile = Join-Path $script:RuntimeRoot 'local-binding-migration.json'
    $recoveryPort = 0
    if (Test-Path -LiteralPath $planFile) {
        $saved = Get-Content -LiteralPath $planFile -Raw | ConvertFrom-Json
        if ($saved.Server -eq $before.Server -and $saved.Cluster -eq $script:ClusterName) {
            $recoveryPort = [int]$saved.ApiPort
        }
    }
    $plan = Get-LocalLoopbackPlan -Bindings $before.Bindings -RecoveryApiPort $recoveryPort
    Write-Host "Project ingress will use 127.0.0.1:8080 and 127.0.0.1:8443; API port stays $($plan.ApiPort)."
    if (-not $Apply) {
        Write-Host 'Preview only. Run again with -Apply during a maintenance window after taking a backup.'
        return
    }
    New-Item -ItemType Directory -Force -Path $script:RuntimeRoot | Out-Null
    # No credentials or container environment are saved. This survives interruption
    # between removal and addition, allowing the same command to finish safely.
    Write-PrivateJson $planFile @{ Cluster = $script:ClusterName; Server = $before.Server; ApiPort = $plan.ApiPort }
    $loadBalancer = "k3d-$script:ClusterName-serverlb"
    if ($plan.Remove.Count) {
        $arguments = @('node', 'edit', $loadBalancer)
        foreach ($port in $plan.Remove) { $arguments += @('--port-delete', $port) }
        Invoke-Native $script:K3d @arguments
        $intermediate = Read-LocalBindingState
        if ($intermediate.Server -ne $before.Server) { throw 'Project server or volumes changed unexpectedly; stopping migration' }
        $remaining = Get-LocalLoopbackPlan -Bindings $intermediate.Bindings -RecoveryApiPort $plan.ApiPort
        if ($remaining.Remove.Count) { throw 'Old bindings remain; inspect the load balancer before retrying' }
        $plan = $remaining
    }
    if ($plan.Add.Count) {
        $arguments = @('node', 'edit', $loadBalancer)
        foreach ($port in $plan.Add) { $arguments += @('--port-add', $port) }
        Invoke-Native $script:K3d @arguments
    }
    $after = Read-LocalBindingState
    if ($after.Server -ne $before.Server) { throw 'Project server or volumes changed unexpectedly; inspect the cluster' }
    $verified = Get-LocalLoopbackPlan -Bindings $after.Bindings -RecoveryApiPort $plan.ApiPort
    if ($verified.Remove.Count -or $verified.Add.Count) { throw 'Loopback bindings are incomplete; rerun this command to finish' }
    Invoke-Native kubectl config set-cluster $script:KubeContext "--server=https://127.0.0.1:$($plan.ApiPort)"
    if (-not (Test-LocalApi)) { throw 'Bindings are local, but the API readiness check failed. Inspect the load balancer; do not recreate the cluster.' }
    Write-Host 'Loopback bindings verified. The project server, its start time, and its volume mounts are unchanged.'
}
