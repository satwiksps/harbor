param([switch]$SkipImport)
. "$PSScriptRoot/common.ps1"
Push-Location $ProjectRoot
try {
    Invoke-Native docker build -t commerce-console:dev -f Dockerfile .
    Invoke-Native docker build -t commerce-dashboard:dev -f dashboard/Dockerfile .
    Invoke-Native docker build -t commerce-wordpress:dev -f docker/wordpress/Dockerfile docker/wordpress
    if (-not $SkipImport) {
        Invoke-Native $K3d image import commerce-console:dev commerce-dashboard:dev commerce-wordpress:dev --cluster $ClusterName
    }
} finally { Pop-Location }
