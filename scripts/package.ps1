param(
    [Parameter(Mandatory=$true)][string]$GameDir,
    [string]$Dotnet = 'dotnet'
)
# Builds the mod and zips a release a player extracts into the game's mods folder:
# STS2_MCP.dll/.json beside a vakuu/ folder of Python code. Left out: any Claude Code copy and local
# settings, caches and logs.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
& (Join-Path $PSScriptRoot 'build.ps1') -GameDir $GameDir -Dotnet $Dotnet
$stage = Join-Path $repoRoot 'build/release'
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Path (Join-Path $stage 'vakuu') -Force | Out-Null
foreach ($name in @('STS2_MCP.dll','STS2_MCP.json')) {
    Copy-Item (Join-Path $repoRoot "build/mod/$name") $stage
}
Get-ChildItem (Join-Path $repoRoot 'vakuu') -File |
    Where-Object { $_.Extension -in '.py','.md' } |
    ForEach-Object { Copy-Item $_.FullName (Join-Path $stage 'vakuu') }
Copy-Item (Join-Path $repoRoot 'docs/安裝說明.md') (Join-Path $stage 'vakuu/安裝說明.md')
Copy-Item (Join-Path $repoRoot 'mod/LICENSE') (Join-Path $stage 'vakuu/STS2MCP-LICENSE.txt')
$version = (git -C $repoRoot describe --tags --always)
$zip = Join-Path $repoRoot "build/vakuu-mod-$version.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip
Write-Output "已打包：$zip"
