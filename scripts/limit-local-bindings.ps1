param([switch]$Apply)
. "$PSScriptRoot/common.ps1"
. "$PSScriptRoot/local-bindings.ps1"
Set-LocalLoopbackBindings -Apply:$Apply
