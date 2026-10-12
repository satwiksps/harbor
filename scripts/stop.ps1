param()
. "$PSScriptRoot/common.ps1"
Invoke-Native $K3d cluster stop $ClusterName
Write-Host 'Project cluster stopped. Its data is retained; bootstrap.ps1 starts it again.'
