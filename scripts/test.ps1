param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    & $Python -X utf8 -c 'import sys,unittest; sys.path.insert(0,"outputs"); suite=unittest.defaultTestLoader.discover("work",pattern="test_*.py"); result=unittest.TextTestRunner().run(suite); sys.exit(not result.wasSuccessful())'
    if ($LASTEXITCODE -ne 0) { throw '測試失敗' }
} finally { Pop-Location }
