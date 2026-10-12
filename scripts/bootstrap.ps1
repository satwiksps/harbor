param([switch]$SkipBuild, [switch]$PrepareOnly)
. "$PSScriptRoot/common.ps1"
& "$PSScriptRoot/new-local-cluster.ps1"
Assert-LocalCluster
New-Item -ItemType Directory -Force -Path $SecretsRoot,$RuntimeRoot | Out-Null
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
Invoke-Native icacls $SecretsRoot /inheritance:r /grant:r "${identity}:(OI)(CI)F" | Out-Null
$credentialFile = Join-Path $SecretsRoot 'development.json'
function New-HexSecret([int]$Bytes = 32) { return [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes($Bytes)).ToLowerInvariant() }
if (Test-Path -LiteralPath $credentialFile) {
    $credentials = Get-Content -LiteralPath $credentialFile -Raw | ConvertFrom-Json -AsHashtable
} else {
    Assert-NoExistingInstallation
    $credentials = @{
        admin_email = 'admin@urumi.local'; admin_password = (New-HexSecret 18)
        wordpress_admin_user = 'storeadmin'; wordpress_admin_password = (New-HexSecret 18)
        postgres_password = (New-HexSecret); mariadb_password = (New-HexSecret)
        mariadb_root_password = (New-HexSecret); session_secret = (New-HexSecret)
        woo_key = ('ck_' + (New-HexSecret 20)); woo_secret = ('cs_' + (New-HexSecret 20))
        upstream_woo = (New-HexSecret); upstream_policies = (New-HexSecret); upstream_shipping = (New-HexSecret)
    }
    Write-PrivateJson -Path $credentialFile -Value $credentials
}
$geminiFile = Join-Path $SecretsRoot 'gemini-api-key.txt'
$geminiKey = if (Test-Path -LiteralPath $geminiFile) { (Get-Content -LiteralPath $geminiFile -Raw).Trim() } else { '' }
$appData = @{}
foreach ($key in @('admin_password','wordpress_admin_password','postgres_password','mariadb_password','mariadb_root_password','session_secret','woo_key','woo_secret','upstream_woo','upstream_policies','upstream_shipping')) { $appData[$key] = $credentials[$key] }
$appData['database_url'] = 'postgresql+psycopg://commerce:' + $credentials.postgres_password + '@postgres:5432/commerce'
$appData['gemini_api_key'] = $geminiKey
$appSecret = @{ apiVersion='v1'; kind='Secret'; metadata=@{name='commerce-app-secrets';namespace=$Namespace}; type='Opaque'; stringData=$appData }
$appFile = Join-Path $SecretsRoot 'k8s-app-secret.json'
Write-PrivateJson -Path $appFile -Value $appSecret
$existingNamespace = & kubectl --context $KubeContext --request-timeout=10s get namespace $Namespace --ignore-not-found -o name
if ($LASTEXITCODE -ne 0) { throw 'Cannot check the project namespace.' }
if (-not $existingNamespace) { Invoke-Native kubectl --context $KubeContext create namespace $Namespace }
Invoke-Native kubectl --context $KubeContext apply -f $appFile
# The registry owns this Secret after initial creation. Never overwrite admin changes.
$existingUpstreamSecret = & kubectl --context $KubeContext --request-timeout=10s -n $Namespace get secret commerce-upstream-credentials --ignore-not-found -o name
if ($LASTEXITCODE -ne 0) { throw 'Cannot check the mutable upstream Secret.' }
if (-not $existingUpstreamSecret) {
    $upstreamSecret = @{apiVersion='v1';kind='Secret';metadata=@{name='commerce-upstream-credentials';namespace=$Namespace};type='Opaque';stringData=@{woo=$credentials.upstream_woo;policies=$credentials.upstream_policies;shipping=$credentials.upstream_shipping}}
    $upstreamFile = Join-Path $SecretsRoot 'k8s-upstream-secret.json'
    Write-PrivateJson -Path $upstreamFile -Value $upstreamSecret
    Invoke-Native kubectl --context $KubeContext create -f $upstreamFile
}
if (-not $PrepareOnly) {
    if (-not $SkipBuild) { & "$PSScriptRoot/build-images.ps1" }
    & "$PSScriptRoot/deploy.ps1"
}
Write-Host "Credentials are stored privately in $credentialFile. No passwords were printed."
Write-Host 'Console: http://console.localhost:8080  Store: http://store.localhost:8080'
