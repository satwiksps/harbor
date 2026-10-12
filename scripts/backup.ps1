param()
. "$PSScriptRoot/common.ps1"
Assert-LocalCluster
$backupRoot = Join-Path $SecretsRoot ('backups/' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $backupRoot | Out-Null
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
Invoke-Native icacls $backupRoot /inheritance:r /grant:r "${identity}:(OI)(CI)F" | Out-Null
function Save-ExecOutput {
    param([string]$File, [string[]]$ExecArguments)
    $start = [System.Diagnostics.ProcessStartInfo]::new()
    $start.FileName = (Get-Command kubectl).Source
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    foreach ($arg in @('--context', $KubeContext, '-n', $Namespace, 'exec') + $ExecArguments) { $start.ArgumentList.Add($arg) }
    $process = [System.Diagnostics.Process]::Start($start)
    $partialFile = $File + '.partial'
    try {
        $destination = [IO.File]::Create($partialFile)
        try { $process.StandardOutput.BaseStream.CopyTo($destination) } finally { $destination.Dispose() }
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { throw "Backup command failed; the incomplete file is marked .partial: $partialFile" }
    } finally {
        # A disk/write failure must not leave this script's kubectl child blocked on stdout.
        if (-not $process.HasExited) { $process.Kill(); $process.WaitForExit(5000) | Out-Null }
        $process.Dispose()
    }
    Move-Item -LiteralPath $partialFile -Destination $File
}
# The database stores credential references; the current mutable Secret must travel with it.
$secretJson = & kubectl --context $KubeContext --request-timeout=15s -n $Namespace get secret commerce-app-secrets commerce-upstream-credentials -o json
if ($LASTEXITCODE -ne 0) { throw 'Cannot back up the project Secrets.' }
$secretSnapshot = $secretJson | ConvertFrom-Json -AsHashtable
foreach ($item in $secretSnapshot.items) {
    $item.metadata = @{name=$item.metadata.name;namespace=$item.metadata.namespace}
}
Write-PrivateJson -Path (Join-Path $backupRoot 'kubernetes-secrets.json') -Value $secretSnapshot
foreach ($filename in @('development.json', 'gemini-api-key.txt')) {
    if (Test-Path -LiteralPath (Join-Path $SecretsRoot $filename)) {
        Copy-Item -LiteralPath (Join-Path $SecretsRoot $filename) -Destination (Join-Path $backupRoot $filename)
    }
}
Save-ExecOutput -File (Join-Path $backupRoot 'gateway.dump') -ExecArguments @('deployment/postgres', '--', 'pg_dump', '-U', 'commerce', '-d', 'commerce', '-Fc')
Save-ExecOutput -File (Join-Path $backupRoot 'wordpress.sql') -ExecArguments @('deployment/mariadb', '--', 'sh', '-c', 'mariadb-dump --single-transaction --user=wordpress --password="$MARIADB_PASSWORD" wordpress')
Save-ExecOutput -File (Join-Path $backupRoot 'wordpress-content.tar.gz') -ExecArguments @('deployment/wordpress', '--', 'tar', '-czf', '-', '-C', '/var/www/html', 'wp-content', 'wp-config.php')
Write-Host "Database, WordPress content and current credential snapshots written privately to $backupRoot"
Write-Host 'Quiesce writes for a consistent cross-database snapshot. Restore is deliberately manual.'
