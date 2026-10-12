param()
. "$PSScriptRoot/common.ps1"
Assert-LocalCluster
New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
$nodes = & kubectl --context $KubeContext get nodes -o json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Could not read the cluster node IP' }
$nodeIP = ($nodes.items[0].status.addresses | Where-Object { $_.type -eq 'InternalIP' }).address
if (-not $nodeIP) { throw 'No InternalIP found for the project cluster' }
$runtimeValues = Join-Path $RuntimeRoot 'values-runtime.yaml'
$revision = [DateTime]::UtcNow.ToString('yyyyMMddHHmmss')
"rolloutRevision: '$revision'`nnetworkPolicy:`n  apiServerNodeCidr: $nodeIP/32`n" | Set-Content -LiteralPath $runtimeValues -Encoding utf8NoBOM
Invoke-Native $Helm lint (Join-Path $ProjectRoot 'deploy/chart') -f (Join-Path $ProjectRoot 'deploy/values-local.yaml') -f $runtimeValues
Invoke-Native $Helm upgrade --install commerce (Join-Path $ProjectRoot 'deploy/chart') --kube-context $KubeContext --namespace $Namespace -f (Join-Path $ProjectRoot 'deploy/values-local.yaml') -f $runtimeValues --wait --timeout 15m
Invoke-Native kubectl --context $KubeContext -n $Namespace wait --for=condition=complete job/wordpress-bootstrap --timeout=600s
Invoke-Native kubectl --context $KubeContext -n $Namespace get pods
