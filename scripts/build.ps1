param(
    [Parameter(Mandatory=$true)][string]$GameDir,
    [string]$Dotnet = 'dotnet'
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$gamePath = (Resolve-Path -LiteralPath $GameDir).Path
foreach ($name in @('sts2.dll','GodotSharp.dll','0Harmony.dll')) {
    if (!(Test-Path -LiteralPath (Join-Path $gamePath "data_sts2_windows_x86_64/$name"))) { throw "缺少遊戲依賴：$name" }
}
$env:DOTNET_CLI_HOME = Join-Path $repoRoot 'build/dotnet-home'
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
& $Dotnet build (Join-Path $repoRoot 'mod/STS2_MCP.csproj') -c Release -o (Join-Path $repoRoot 'build/mod') "-p:STS2GameDir=$gamePath" --nologo
if ($LASTEXITCODE -ne 0) { throw '模組建置失敗' }
Copy-Item -LiteralPath (Join-Path $repoRoot 'mod/STS2_MCP.json') -Destination (Join-Path $repoRoot 'build/mod/STS2_MCP.json') -Force
