# Preparation only: no install, credential creation, or task registration.
# BatchProgram/BatchArguments must be the actual documented/configured Navicat
# batch-job invocation. This script does not invent a Navicat CLI or read its
# saved connection secrets. Windows must remain running for scheduled updates.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$BatchProgram,
    [string]$BatchJobName,
    [string[]]$BatchArguments = @(),
    [Parameter(Mandatory=$true)][string]$ExportPath,
    [Parameter(Mandatory=$true)][string]$PythonPath,
    [Parameter(Mandatory=$true)][string]$GitPath,
    [Parameter(Mandatory=$true)][string]$SshPath,
    [Parameter(Mandatory=$true)][string]$KeyFile,
    [Parameter(Mandatory=$true)][string]$KnownHostsFile,
    [Parameter(Mandatory=$true)][string]$RepoDir,
    [Parameter(Mandatory=$true)][string]$SnapshotDir,
    [Parameter(Mandatory=$true)][string]$AuthorName,
    [Parameter(Mandatory=$true)][string]$AuthorEmail,
    [int]$MaxAgeMinutes = 60,
    [int]$WaitSeconds = 300,
    [int]$StableSeconds = 5
)

$ErrorActionPreference = 'Stop'
$RunLockStream = $null
$RunLockPath = $null

function ConvertTo-NativeArgument([string]$Value) {
    # Start-Process joins ArgumentList; explicitly apply Windows argv quoting.
    # Keeps spaces in executable-independent arguments safe on PowerShell 5.1.
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
    $Escaped = [regex]::Replace($Value, '(\\*)"', [Text.RegularExpressions.MatchEvaluator]{
        param($Match)
        $Slashes = $Match.Groups[1].Value
        return $Slashes + $Slashes + '\"'
    })
    $Escaped = [regex]::Replace($Escaped, '(\\+)$', '$1$1')
    return '"' + $Escaped + '"'
}

try {
    foreach ($RequiredPath in @($BatchProgram, $PythonPath, $GitPath, $SshPath, $KeyFile, $KnownHostsFile)) {
        if (-not (Test-Path -LiteralPath $RequiredPath -PathType Leaf)) {
            throw 'Missing configured runtime file.'
        }
    }
    $PublisherPath = Join-Path $PSScriptRoot 'publish_windows.py'
    if (-not (Test-Path -LiteralPath $PublisherPath -PathType Leaf)) {
        throw 'Missing publisher preparation file.'
    }
    if (-not [string]::IsNullOrWhiteSpace($BatchJobName)) {
        if ($BatchArguments.Count -gt 0) { throw 'Choose the saved batch name or explicit arguments, not both.' }
        # Navicat 17 official syntax: navicat.exe -batchjob BatchJobName.
        $ActualBatchArguments = @('-batchjob', $BatchJobName)
    }
    elseif ($BatchArguments.Count -gt 0) {
        $ActualBatchArguments = $BatchArguments
    }
    else { throw 'A verified saved Navicat batch job is required.' }

    # Prevent overlapping exports as well as overlapping publishing, including
    # independently started copies. Does not read a Navicat connection setting.
    $FullRepoDir = [IO.Path]::GetFullPath($RepoDir)
    $RepoParent = [IO.Path]::GetDirectoryName($FullRepoDir.TrimEnd([char[]]@('\', '/')))
    [IO.Directory]::CreateDirectory($RepoParent) | Out-Null
    $RunLockPath = $FullRepoDir.TrimEnd([char[]]@('\', '/')) + '.dashboard-run.lock'
    # OS ownership belongs to the live handle, not file existence. A crash or
    # restart releases the handle; leaving the small file cannot block recovery.
    $RunLockStream = [IO.File]::Open($RunLockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::Write, [IO.FileShare]::None)
    # Record before launching the real batch. Waiting over one second avoids
    # rejecting a new SQL queried_at truncated to seconds in the same second.
    $RunStartedAt = [DateTimeOffset]::UtcNow
    Start-Sleep -Milliseconds 1100
    $NativeArguments = ($ActualBatchArguments | ForEach-Object { ConvertTo-NativeArgument ([string]$_) }) -join ' '
    # Navicat may hand off to its already-open GUI and exit before export, or
    # keep its GUI running after export. Neither case proves batch completion.
    # Start asynchronously and let the file/query guards prove a new result.
    $BatchProcess = Start-Process -FilePath $BatchProgram -ArgumentList $NativeArguments -PassThru
    if ($BatchProcess.HasExited -and $BatchProcess.ExitCode -ne 0) {
        throw 'Navicat batch did not finish successfully.'
    }
    # A GUI command may return before exporting. The publisher waits for a fresh
    # mtime, a stable complete file and queried_at after RunStartedAt.
    $PublishArguments = @(
        $PublisherPath,
        '--input', $ExportPath,
        '--snapshot-dir', $SnapshotDir,
        '--repo-dir', $RepoDir,
        '--git-path', $GitPath,
        '--ssh-path', $SshPath,
        '--key-file', $KeyFile,
        '--known-hosts-file', $KnownHostsFile,
        '--started-at', $RunStartedAt.ToString('o'),
        '--author-name', $AuthorName,
        '--author-email', $AuthorEmail,
        '--max-age-minutes', [string]$MaxAgeMinutes,
        '--wait-seconds', [string]$WaitSeconds,
        '--stable-seconds', [string]$StableSeconds,
        '--minimum-publish-interval-minutes', '10'
    )
    & $PythonPath @PublishArguments
    if ($LASTEXITCODE -ne 0) {
        throw 'Validated snapshot publishing did not complete.'
    }
    exit 0
}
catch {
    # Do not echo command arguments, export records, credentials or raw errors.
    Write-Error '同步未完成；未宣称网站已更新，原成功网站快照保留。请由维护人员检查本轮导出及发布状态。' -ErrorAction Continue
    exit 1
}
finally {
    if ($null -ne $RunLockStream) { $RunLockStream.Dispose() }
}
