param()
. "$PSScriptRoot/common.ps1"
. "$PSScriptRoot/local-bindings.ps1"
if (-not (Test-Path -LiteralPath $K3d) -or -not (Test-Path -LiteralPath $Helm)) { & "$PSScriptRoot/install-tools.ps1" }
Invoke-Native docker info --format '{{.ServerVersion}}'
$clusters = & $K3d cluster list -o json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Could not list k3d clusters' }
if ($clusters | Where-Object { $_.name -eq $ClusterName }) {
    $bindingPlan = Get-LocalLoopbackPlan -Bindings (Read-LocalBindingState).Bindings
    if ($bindingPlan.Remove.Count -or $bindingPlan.Add.Count) {
        throw 'This cluster has non-loopback bindings. Back up, then run scripts/limit-local-bindings.ps1 -Apply before resuming.'
    }
    Invoke-Native $K3d cluster start $ClusterName --wait --timeout 180s
} else {
    $apiPort = Get-AvailableLoopbackPort
    Invoke-Native $K3d cluster create $ClusterName --image rancher/k3s:v1.35.5-k3s1 --servers 1 --agents 0 --api-port "127.0.0.1:$apiPort" --port '127.0.0.1:8080:80@loadbalancer' --port '127.0.0.1:8443:443@loadbalancer' --servers-memory 6g --k3s-arg '--disable=metrics-server@server:0' --k3s-arg '--secrets-encryption@server:0' --wait --timeout 180s
}
$bindingPlan = Get-LocalLoopbackPlan -Bindings (Read-LocalBindingState).Bindings
if ($bindingPlan.Remove.Count -or $bindingPlan.Add.Count) { throw 'Local port bindings could not be verified' }
Invoke-Native kubectl config set-cluster $KubeContext "--server=https://127.0.0.1:$($bindingPlan.ApiPort)"
Invoke-Native kubectl config use-context $KubeContext
Repair-LocalApiProxy
Invoke-Native kubectl --context $KubeContext wait --for=condition=Ready nodes --all --timeout=180s
Write-Host 'Project cluster ready. Other Docker resources were not changed.'
