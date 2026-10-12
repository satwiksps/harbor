param()
. "$PSScriptRoot/common.ps1"
$ProgressPreference = 'SilentlyContinue'
$downloadRoot = Join-Path $ToolsRoot 'downloads'
New-Item -ItemType Directory -Force -Path $downloadRoot | Out-Null
$k3dVersion = 'v5.9.0'
$helmVersion = 'v4.3.1'
if (-not (Test-Path -LiteralPath $K3d)) {
    Invoke-WebRequest "https://github.com/k3d-io/k3d/releases/download/$k3dVersion/k3d-windows-amd64.exe" -OutFile $K3d
}
Invoke-WebRequest "https://github.com/k3d-io/k3d/releases/download/$k3dVersion/checksums.txt" -OutFile "$downloadRoot/k3d-checksums.txt"
$line = Get-Content "$downloadRoot/k3d-checksums.txt" | Where-Object { $_ -match 'k3d-windows-amd64.exe$' }
if (-not $line) { throw 'Official k3d checksum entry was not found' }
$expected = ($line -split '\s+')[0]
if ((Get-FileHash -LiteralPath $K3d -Algorithm SHA256).Hash -ne $expected) { throw 'k3d checksum mismatch' }
Invoke-WebRequest "https://get.helm.sh/helm-$helmVersion-windows-amd64.zip" -OutFile "$downloadRoot/helm.zip"
Invoke-WebRequest "https://get.helm.sh/helm-$helmVersion-windows-amd64.zip.sha256sum" -OutFile "$downloadRoot/helm.sha256sum"
$expected = ((Get-Content "$downloadRoot/helm.sha256sum" -Raw) -split '\s+')[0]
if ((Get-FileHash -LiteralPath "$downloadRoot/helm.zip" -Algorithm SHA256).Hash -ne $expected) { throw 'Helm checksum mismatch' }
Expand-Archive -LiteralPath "$downloadRoot/helm.zip" -DestinationPath "$downloadRoot/helm" -Force
Copy-Item -LiteralPath "$downloadRoot/helm/windows-amd64/helm.exe" -Destination $Helm -Force
Invoke-Native $K3d version
Invoke-Native $Helm version --short
Write-Host 'Installed under .tools only. The system PATH was not changed.'
