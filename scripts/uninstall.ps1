param()
. "$PSScriptRoot/common.ps1"
Assert-LocalCluster
Invoke-Native $Helm uninstall commerce --kube-context $KubeContext --namespace $Namespace --wait --timeout 5m
Invoke-Native kubectl --context $KubeContext -n $Namespace delete job wordpress-bootstrap --ignore-not-found
Write-Host 'Application workloads removed. Namespace, Secrets and persistent volumes are retained.'
Write-Host 'Run bootstrap.ps1 to reinstall with the existing data. This script never deletes the cluster.'
