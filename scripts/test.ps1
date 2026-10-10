param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    # Windows PowerShell 5.1 strips embedded double quotes from native arguments, so avoid inline -c code.
    $env:PYTHONPATH = Join-Path $repoRoot 'vakuu'
    & $Python -X utf8 -m unittest discover -s tests -p 'test_*.py'
    if ($LASTEXITCODE -ne 0) { throw '測試失敗' }
} finally { Pop-Location }
