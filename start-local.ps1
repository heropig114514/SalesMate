<#
职责：提供 Windows 本地工作台的一键启动、状态查询与停止入口。
实现：定位仓库，准备 Python 虚拟环境与原依赖清单，再调用 local_server.py 管理后台进程。
关联：backend/tools/local_server.py、requirements.txt、根 .env；不修改现有数据库或模型配置。
目录：无函数或类。
变量索引：Action 选择 start/status/stop；WslDistro 显式指定数据库所在 WSL；NoBrowser 禁止打开浏览器；
Python 指定首次创建环境的解释器；ProjectRoot、VenvPython、Launcher 为绝对路径；LauncherArgs 为传入参数；
ErrorActionPreference 使 PowerShell 错误终止；InstallLog、InstallErrorLog 保存依赖安装诊断；InstallProcess 为安装进程；DependencyHash、HashPath 用于发现依赖清单变化；
RequirementFiles 为实际递归引用的四份清单；HashInput、Hasher 用于计算内容摘要；SavedHash 为上次安装记录。
约束：要求已安装 Python 3.11+；数据库须已配置，WSL 必须已安装；不自动重试安装，不重设账号，不改变分析模式。
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
            # 显式 UTF-8 读取含中文说明的清单；独立重定向避免 PowerShell 5 将 stderr 误判为终止错误。
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
