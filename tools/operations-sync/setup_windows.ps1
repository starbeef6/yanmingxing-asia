# Download only the three public runtime files. This does not install Python or
# Git, execute any downloaded tool, generate a key, set permissions, or schedule.
# No tokens, saved connection settings or private keys are read by this script.
[CmdletBinding()]
param(
    [string]$Destination = 'C:\Users\1\Documents\OperationsDashboard-20261010',
    [string]$Revision = 'main'
)

$ErrorActionPreference = 'Stop'
$OriginalTls = [Net.ServicePointManager]::SecurityProtocol
$StageDirectory = $null
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    if ($Revision -ne 'main' -and $Revision -notmatch '^[0-9a-fA-F]{40}$') {
        throw 'Use main or an explicit reviewed commit SHA.'
    }
    $Destination = [IO.Path]::GetFullPath($Destination)
    [IO.Directory]::CreateDirectory($Destination) | Out-Null
    if ((Get-Item -LiteralPath $Destination).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Use a dedicated normal directory, not a junction or symbolic link.'
    }
    # Resolve main once, then download all files from that same public revision.
    $Commit = Invoke-RestMethod -Uri ('https://api.github.com/repos/starbeef6/yanmingxing-asia/commits/' + $Revision) -Headers @{'User-Agent'='OperationsDashboard-PublicSetup'}
    $CommitSha = [string]$Commit.sha
    if ($CommitSha -notmatch '^[0-9a-f]{40}$') { throw 'Invalid public revision.' }
    $Files = @('convert_snapshot.py', 'publish_windows.py', 'sync_windows.ps1')
    $StageDirectory = Join-Path $Destination ('.setup-' + [Guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory($StageDirectory) | Out-Null
    foreach ($Name in $Files) {
        $Uri = 'https://raw.githubusercontent.com/starbeef6/yanmingxing-asia/' + $CommitSha + '/tools/operations-sync/' + $Name
        $TemporaryPath = Join-Path $StageDirectory $Name
        Invoke-WebRequest -UseBasicParsing -Uri $Uri -OutFile $TemporaryPath -Headers @{'User-Agent'='OperationsDashboard-PublicSetup'} | Out-Null
        if ((Get-Item -LiteralPath $TemporaryPath).Length -eq 0) { throw 'An empty public file was downloaded.' }
    }
    foreach ($Name in $Files) {
        $Target = Join-Path $Destination $Name
        if (Test-Path -LiteralPath $Target) {
            if ((Get-Item -LiteralPath $Target).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Refuse to replace a symbolic link.'
            }
        }
        Move-Item -LiteralPath (Join-Path $StageDirectory $Name) -Destination $Target -Force
    }
    $Configuration = @"
Public tool revision: $CommitSha
Tool directory: $Destination
Navicat executable: G:\Navicat Premium 17.0.8 (x64)\Navicat Premium 17\navicat.exe
Saved Navicat batch job: ODSync20261010A
Export input: C:\Users\1\Desktop\ODDaily20261010A.json
Python executable: C:\Users\1\AppData\Local\Programs\Python\Python313\python.exe
Local snapshots: $Destination\snapshots
Dedicated empty publish repository: $Destination\publish-repo

Maintainer still supplies the actual PortableGit git.exe and ssh.exe paths,
an already-approved repository-specific deploy-key PATH, a verified GitHub
known_hosts PATH, and a GitHub-associated commit author name/email.
The export and key/known_hosts files must stay outside snapshots/publish-repo.

After one real successful export, query-time/clock check, push, Pages build and
public JSON verification: schedule sync_windows.ps1 every ten minutes, using
the same Windows user 1 and never starting another task instance concurrently.
This setup has NOT configured keys, permission, execution policy or schedules.
It has NOT connected to a database, run a query, or published business data.
"@
    $Encoding = New-Object System.Text.UTF8Encoding($true)
    [IO.File]::WriteAllText((Join-Path $Destination 'PATHS_TO_CONFIGURE.txt'), $Configuration, $Encoding)
    Write-Host ('Public tools saved to ' + $Destination + '; remaining paths are in PATHS_TO_CONFIGURE.txt. No scheduling or publishing was performed.')
}
catch {
    Write-Error 'Public tool setup did not finish. No keys, permissions or schedules were configured.' -ErrorAction Continue
    exit 1
}
finally {
    [Net.ServicePointManager]::SecurityProtocol = $OriginalTls
    if ($null -ne $StageDirectory -and (Test-Path -LiteralPath $StageDirectory)) {
        Remove-Item -LiteralPath $StageDirectory -Recurse -Force -ErrorAction SilentlyContinue
    }
}
