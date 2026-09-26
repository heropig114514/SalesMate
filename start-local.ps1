<#
Responsibility: Provide a one-command Windows local-workspace start, status, and stop entry point.
Implementation: Locate the repository, prepare a Python virtual environment and the original dependency manifest, then call local_server.py to manage background processes.
Relationships: backend/tools/local_server.py, requirements.txt, and root .env. The launcher does not modify existing database or model configuration.
Directory:
- None
Variable index:
- Action: Selects start, status, or stop.
- WslDistro: Explicitly identifies the WSL distribution hosting the database.
- NoBrowser: Prevents opening a browser.
- Python: Interpreter for the initial environment.
- ProjectRoot/VenvPython/Launcher: Absolute paths.
- LauncherArgs: Forwarded arguments.
- ErrorActionPreference: Makes PowerShell errors terminating.
- InstallLog/InstallErrorLog: Dependency-installation diagnostics.
- InstallProcess: Installation process.
- DependencyHash/HashPath: Detect dependency-manifest changes.
- RequirementFiles: The four recursively referenced manifests.
- HashInput/Hasher: Calculate the content digest.
- SavedHash: Previous installation record.
Constraints: Requires Python 3.11+, a configured database, and installed WSL. It does not retry installation, reset accounts, or change analysis mode.
#>
[CmdletBinding()]
param(
    [ValidateSet('start', 'status', 'stop')][string]$Action = 'start',
    [string]$WslDistro,
    [switch]$NoBrowser,
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot '.venv/Scripts/python.exe'
$Launcher = Join-Path $ProjectRoot 'backend/tools/local_server.py'

try {
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        if ($Action -ne 'start') { throw 'No local environment exists. Run start first.' }
        & $Python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or later is required. Use -Python with its full path.' }
        & $Python -m venv (Join-Path $ProjectRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
    }
    if ($Action -eq 'start') {
        $RequirementFiles = @('requirements.txt', 'backend/requirements/dev.txt', 'backend/requirements/base.txt', 'agent/requirements.txt')
        $HashInput = ($RequirementFiles | ForEach-Object { Get-Content -LiteralPath (Join-Path $ProjectRoot $_) -Raw }) -join "`n"
        $Hasher = [System.Security.Cryptography.SHA256]::Create()
        try { $DependencyHash = [BitConverter]::ToString($Hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($HashInput))) }
        finally { $Hasher.Dispose() }
        $HashPath = Join-Path $ProjectRoot '.venv/salesmate-requirements.sha256'
        $SavedHash = if (Test-Path -LiteralPath $HashPath) { (Get-Content -LiteralPath $HashPath -Raw).Trim() } else { '' }
        if ($SavedHash -ne $DependencyHash) {
            $InstallLog = Join-Path $ProjectRoot '.venv/salesmate-install.log'
            $InstallErrorLog = Join-Path $ProjectRoot '.venv/salesmate-install-error.log'
            Write-Host "[setup] Installing project dependencies. Log: $InstallLog"
            # Read manifests explicitly as UTF-8. Separate redirection prevents PowerShell 5 from misclassifying stderr as a terminating error.
            $InstallProcess = Start-Process -FilePath $VenvPython -ArgumentList @('-X', 'utf8', '-m', 'pip', 'install', '--disable-pip-version-check', '--retries', '0', '-r', ('"' + (Join-Path $ProjectRoot 'requirements.txt') + '"')) -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput $InstallLog -RedirectStandardError $InstallErrorLog
            if ($InstallProcess.ExitCode -ne 0) { throw "Dependency installation failed. Inspect $InstallLog and $InstallErrorLog" }
            Set-Content -LiteralPath $HashPath -Value $DependencyHash -Encoding ASCII
        }
        & $VenvPython -m pip check
        if ($LASTEXITCODE -ne 0) { throw 'Dependency consistency check failed.' }
    }
    $LauncherArgs = @($Launcher, $Action)
    if ($WslDistro) { $LauncherArgs += @('--wsl-distro', $WslDistro) }
    if ($NoBrowser) { $LauncherArgs += '--no-browser' }
    & $VenvPython -X utf8 @LauncherArgs
    exit $LASTEXITCODE
} catch {
    Write-Error -ErrorAction Continue "Local launcher failed: $($_.Exception.Message)"
    exit 1
}
