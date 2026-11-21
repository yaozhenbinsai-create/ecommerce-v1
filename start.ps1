param([int]$Port = 8770, [int]$DatabasePort = 13320, [string]$MySql = '', [switch]$Prepare)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$studioPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$studioArgs = @((Join-Path $PSScriptRoot 'run.py'), '--port', $Port, '--db-port', $DatabasePort)
if ($MySql) { $studioArgs += @('--mysql', $MySql) }
if ($Prepare) { $studioArgs += '--prepare' }
if (-not (Test-Path -LiteralPath $studioPython)) {
    throw '请先在项目目录执行 py -3 -m venv .venv，再用 .venv\Scripts\python -m pip install -r requirements.lock.txt 安装依赖。'
}
& $studioPython @studioArgs
exit $LASTEXITCODE
