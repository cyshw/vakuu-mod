param(
    [Parameter(Mandatory=$true)][string]$GameDir,
    [Parameter(Mandatory=$true)][string]$Pythonw
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$gamePath = (Resolve-Path -LiteralPath $GameDir).Path
$pythonPath = (Resolve-Path -LiteralPath $Pythonw).Path
if (!(Test-Path -LiteralPath (Join-Path $gamePath 'SlayTheSpire2.exe'))) { throw '這不是 Windows 版殺戮尖塔 2 安裝資料夾' }
if (Get-Process -Name SlayTheSpire2 -ErrorAction SilentlyContinue) { throw '請先正常退出遊戲再安裝' }
$buildPath = Join-Path $repoRoot 'build/mod'
foreach ($name in @('STS2_MCP.dll','STS2_MCP.json')) {
    if (!(Test-Path -LiteralPath (Join-Path $buildPath $name))) { throw '請先執行 build.ps1' }
}
$config = @{
    python = $pythonPath
    script = Join-Path $repoRoot 'outputs/spire-buttons.py'
    port = 15527
    preview_path = Join-Path $repoRoot 'build/遊戲內按鈕預覽.png'
}
$configPath = Join-Path $buildPath 'STS2_CodexPanel.conf'
$json = $config | ConvertTo-Json
[System.IO.File]::WriteAllText($configPath, $json, [System.Text.UTF8Encoding]::new($false))
$modPath = Join-Path $gamePath 'mods'
New-Item -ItemType Directory -Path $modPath -Force | Out-Null
foreach ($name in @('STS2_MCP.dll','STS2_MCP.json','STS2_CodexPanel.conf')) {
    Copy-Item -LiteralPath (Join-Path $buildPath $name) -Destination (Join-Path $modPath $name) -Force
}
Write-Output '呼叫瓦庫已安裝。重開遊戲後載入。'
