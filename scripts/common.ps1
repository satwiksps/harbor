$ErrorActionPreference = 'Stop'
$script:ProjectRoot = Split-Path -Parent $PSScriptRoot
$script:ToolsRoot = Join-Path $script:ProjectRoot '.tools'
$script:SecretsRoot = Join-Path $script:ProjectRoot '.secrets'
$script:RuntimeRoot = Join-Path $script:ProjectRoot '.runtime'
$script:K3d = Join-Path $script:ToolsRoot 'k3d.exe'
$script:Helm = Join-Path $script:ToolsRoot 'helm.exe'
$script:ClusterName = 'urumi-commerce'
$script:KubeContext = 'k3d-urumi-commerce'
$script:Namespace = 'commerce'
function Invoke-Native {
    param([Parameter(Mandatory)][string]$Command, [Parameter(ValueFromRemainingArguments)][string[]]$Arguments)
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Command exited with code $LASTEXITCODE" }
}
function Assert-LocalCluster {
    $current = & kubectl config current-context
    if ($LASTEXITCODE -ne 0 -or $current.Trim() -ne $script:KubeContext) {
        throw "Select the project cluster first: kubectl config use-context $script:KubeContext"
    }
}
function Test-LocalApi {
    for ($attempt = 0; $attempt -lt 6; $attempt++) {
        & kubectl --context $script:KubeContext --request-timeout=5s get --raw /readyz *> $null
        if ($LASTEXITCODE -eq 0) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}
function Repair-LocalApiProxy {
    if (Test-LocalApi) { return }
    # A resumed node can receive a new Docker IP while its proxy caches the old one.
    & docker exec "k3d-$script:ClusterName-server-0" kubectl --request-timeout=5s get --raw /readyz *> $null
    if ($LASTEXITCODE -ne 0) { throw 'The project API is not ready inside its node. Inspect that node before retrying.' }
    Write-Host 'Project API is healthy internally; refreshing only its load balancer.'
    Invoke-Native docker restart "k3d-$script:ClusterName-serverlb"
    if (-not (Test-LocalApi)) { throw 'The project API is still unreachable after refreshing its load balancer.' }
}
function Assert-NoExistingInstallation {
    # Lost local credentials must never silently replace passwords for retained data.
    $existing = & kubectl --context $script:KubeContext --request-timeout=10s -n $script:Namespace get secret/commerce-app-secrets secret/commerce-upstream-credentials pvc/wordpress-data pvc/mariadb-data pvc/postgres-data --ignore-not-found -o name
    if ($LASTEXITCODE -ne 0) { throw 'Cannot check for an existing installation. Refusing to generate replacement credentials.' }
    if ($existing) { throw 'Existing project Secrets or volumes were found. Restore .secrets/development.json from your private backup before bootstrapping.' }
}
function Write-PrivateJson {
    param([string]$Path, [object]$Value)
    $Value | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $Path -Encoding utf8NoBOM
}
