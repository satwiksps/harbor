param([switch]$SkipImport)
. "$PSScriptRoot/common.ps1"
Push-Location $ProjectRoot
try {
    Invoke-Native docker build -t harbor:dev -f Dockerfile .
    Invoke-Native docker build -t harbor-dashboard:dev -f dashboard/Dockerfile .
    Invoke-Native docker build -t harbor-wordpress:dev -f docker/wordpress/Dockerfile docker/wordpress
    if (-not $SkipImport) {
        Invoke-Native $K3d image import harbor:dev harbor-dashboard:dev harbor-wordpress:dev --cluster $ClusterName
    }
} finally { Pop-Location }
