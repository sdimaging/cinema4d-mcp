param(
    [Parameter(Mandatory=$true)][string]$PluginsDirectory,
    [string]$Executable = 'C:\Program Files\Maxon Cinema 4D 2026\Cinema 4D.exe',
    [switch]$Restart,
    [switch]$Start,
    [int]$Port = 5555
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$destination = (Resolve-Path -LiteralPath $PluginsDirectory).Path
if (!(Test-Path -LiteralPath $destination -PathType Container)) { throw 'PluginsDirectory must already exist.' }

function Invoke-C4DRequest([hashtable]$Request, [switch]$AllowDisconnect) {
    if ($env:MCP_AUTH_TOKEN) { $Request.auth_token = $env:MCP_AUTH_TOKEN }
    $client = [Net.Sockets.TcpClient]::new()
    try {
        $client.Connect('127.0.0.1', $Port)
        $client.ReceiveTimeout = 15000
        $client.SendTimeout = 15000
        $stream = $client.GetStream()
        $bytes = [Text.Encoding]::UTF8.GetBytes(($Request | ConvertTo-Json -Compress -Depth 10) + "`n")
        $stream.Write($bytes, 0, $bytes.Length)
        $reader = [IO.StreamReader]::new($stream, [Text.Encoding]::UTF8)
        $line = $reader.ReadLine()
        if (!$line) {
            if ($AllowDisconnect) { return $null }
            throw 'C4D disconnected before replying.'
        }
        $reply = $line | ConvertFrom-Json
        if ($reply.error) { throw $reply.error }
        return $reply
    } finally { $client.Dispose() }
}

$running = @(Get-Process -Name 'Cinema 4D' -ErrorAction SilentlyContinue)
$reopen = @()
if ($running.Count) {
    if (!$Restart) { throw 'Close C4D normally first, or use -Restart. Python plugin reload is not supported.' }
    if ($running.Count -ne 1) { throw 'More than one C4D process: close the non-target instance first.' }
    # Refuse dirty documents. Never dismiss a save dialog or force-kill.
    $guard = @'
import c4d, json
def inspect():
    docs=[]; dirty=[]; d=c4d.documents.GetFirstDocument()
    while d:
        if d.GetChanged(): dirty.append(d.GetDocumentName())
        if d.GetDocumentPath(): docs.append(str(d.GetDocumentPath()) + '/' + d.GetDocumentName())
        d=d.GetNext()
    if dirty: raise RuntimeError('Save documents before restart: ' + ', '.join(dirty))
    print('REOPEN_JSON=' + json.dumps(docs))
inspect()
'@
    $state = Invoke-C4DRequest @{command='execute_python'; script=$guard}
    $match = [regex]::Match($state.output, 'REOPEN_JSON=(\[[^\r\n]*\])')
    if (!$match.Success) { throw 'Could not verify document state; refusing restart.' }
    $reopen = @($match.Groups[1].Value | ConvertFrom-Json)
    $quit = @'
import c4d
def quit_clean():
    d=c4d.documents.GetFirstDocument()
    while d:
        if d.GetChanged(): raise RuntimeError('Document became dirty; restart cancelled')
        d=d.GetNext()
    c4d.CallCommand(12104)
quit_clean()
'@
    try { Invoke-C4DRequest @{command='execute_python'; script=$quit} -AllowDisconnect | Out-Null }
    catch { Write-Warning "Quit response unavailable; checking actual process exit. $($_.Exception.Message)" }
    $deadline = [DateTime]::UtcNow.AddSeconds(45)
    while ((Get-Process -Id $running[0].Id -ErrorAction SilentlyContinue) -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 500
    }
    if (Get-Process -Id $running[0].Id -ErrorAction SilentlyContinue) {
        throw 'C4D has not exited. Resolve its save/startup dialog; no files were replaced.'
    }
}

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss_fff'
$backup = Join-Path (Split-Path -Parent $destination) "mcp-backups\$stamp"
New-Item -ItemType Directory -Path $backup | Out-Null
function Copy-Verified([string]$Source, [string]$Relative) {
    $target = Join-Path $destination $Relative
    $saved = Join-Path $backup $Relative
    if (Test-Path -LiteralPath $target -PathType Leaf) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $saved) | Out-Null
        Copy-Item -LiteralPath $target -Destination $saved
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
    Copy-Item -LiteralPath $Source -Destination $target -Force
    if ((Get-FileHash -LiteralPath $Source).Hash -ne (Get-FileHash -LiteralPath $target).Hash) {
        throw "Hash verification failed: $Relative. Backup: $backup"
    }
}
Copy-Verified (Join-Path $repoRoot 'c4d_plugin\mcp_server_plugin.pyp') 'mcp_server_plugin.pyp'
$patterns = Join-Path $repoRoot 'c4d_plugin\scene_nodes_patterns.py'
if (Test-Path -LiteralPath $patterns) { Copy-Verified $patterns 'scene_nodes_patterns.py' }
foreach ($folder in @('data', 'docs')) {
    $sourceFolder = Join-Path $repoRoot $folder
    foreach ($file in Get-ChildItem -LiteralPath $sourceFolder -File -Recurse) {
        if (($folder -eq 'data' -and $file.Extension -eq '.json') -or
            ($folder -eq 'docs' -and $file.Extension -eq '.md')) {
            $relative = $file.FullName.Substring($repoRoot.Length + 1)
            Copy-Verified $file.FullName $relative
        }
    }
}
$expectedHash = (Get-FileHash -LiteralPath (Join-Path $destination 'mcp_server_plugin.pyp')).Hash.ToLowerInvariant()
Write-Output "Installed SHA256 $expectedHash; recoverable backup: $backup"
if (!($Start -or $Restart)) { return }
$launch = [Diagnostics.ProcessStartInfo]::new()
$launch.FileName = (Resolve-Path -LiteralPath $Executable).Path
$launch.WorkingDirectory = Split-Path -Parent $launch.FileName
$launch.UseShellExecute = $false
$launch.EnvironmentVariables['C4D_MCP_AUTOSTART'] = '1'
$process = [Diagnostics.Process]::Start($launch)
$deadline = [DateTime]::UtcNow.AddSeconds(120)
$ready = $false
while ([DateTime]::UtcNow -lt $deadline) {
    try {
        $ping = Invoke-C4DRequest @{command='ping'}
        if ($ping.process_id -eq $process.Id -and $ping.loaded_source_sha256 -eq $expectedHash) {
            $ready = $true; break
        }
    } catch {}
    if ($process.HasExited) { throw 'C4D exited during startup.' }
    Start-Sleep -Milliseconds 750
}
if (!$ready) { throw 'C4D did not report the expected PID + loaded source hash. Check startup dialogs; no force-kill performed.' }
if ($reopen.Count) {
    $encoded = ConvertTo-Json -Compress -InputObject @($reopen)
    $encodedLiteral = ConvertTo-Json -Compress -InputObject $encoded
    $script = "import c4d, json`nfor path in json.loads($encodedLiteral):`n if not c4d.documents.LoadFile(path): raise RuntimeError('Failed to reopen saved document')"
    Invoke-C4DRequest @{command='execute_python'; script=$script} | Out-Null
}
Write-Output "Ready: PID $($ping.process_id), build $($ping.build_id), loaded SHA256 verified."
