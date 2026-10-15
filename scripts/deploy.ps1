param()
. "$PSScriptRoot/common.ps1"
Assert-LocalCluster
New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
$nodes = & kubectl --context $KubeContext get nodes -o json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Could not read the cluster node IP' }
$nodeIP = ($nodes.items[0].status.addresses | Where-Object { $_.type -eq 'InternalIP' }).address
if (-not $nodeIP) { throw 'No InternalIP found for the project cluster' }
$podCidrs = @($nodes.items[0].spec.podCIDRs)
if (-not $podCidrs.Count -or -not $podCidrs[0]) { throw 'No pod CIDR found for the project ingress node' }
$runtimeValues = Join-Path $RuntimeRoot 'values-runtime.yaml'
$revision = [DateTime]::UtcNow.ToString('yyyyMMddHHmmss')
$credentials = Get-Content -LiteralPath (Join-Path $SecretsRoot 'development.json') -Raw | ConvertFrom-Json
# JSON is valid YAML and safely quotes the retained administrator identity.
@{
    rolloutRevision = $revision
    networkPolicy = @{ apiServerNodeCidr = "$nodeIP/32" }
    gateway = @{ trustedProxyCidrs = $podCidrs; adminEmail = $credentials.admin_email }
    postgres = @{ user = $PostgresUser; database = $PostgresDatabase }
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $runtimeValues -Encoding utf8NoBOM
Invoke-Native $Helm lint (Join-Path $ProjectRoot 'deploy/chart') -f (Join-Path $ProjectRoot 'deploy/values-local.yaml') -f $runtimeValues
Invoke-Native $Helm upgrade --install $ReleaseName (Join-Path $ProjectRoot 'deploy/chart') --kube-context $KubeContext --namespace $Namespace -f (Join-Path $ProjectRoot 'deploy/values-local.yaml') -f $runtimeValues --wait --timeout 15m
Invoke-Native kubectl --context $KubeContext -n $Namespace wait --for=condition=complete job/wordpress-bootstrap --timeout=600s
Invoke-Native kubectl --context $KubeContext -n $Namespace get pods
