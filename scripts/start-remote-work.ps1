param(
    [int]$GamePort = 5000,
    [int]$OpenCodePort = 4096,
    [string]$HostAddress = "auto"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PowerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

$GameScript = Join-Path $PSScriptRoot "start-game.ps1"
$OpenCodeScript = Join-Path $PSScriptRoot "start-opencode-server.ps1"

Start-Process -FilePath $PowerShell -WorkingDirectory $ProjectRoot -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy", "Bypass",
    "-File", $GameScript,
    "-Port", $GamePort,
    "-HostAddress", $HostAddress
)

Start-Process -FilePath $PowerShell -WorkingDirectory $ProjectRoot -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy", "Bypass",
    "-File", $OpenCodeScript,
    "-Port", $OpenCodePort,
    "-HostAddress", $HostAddress
)

"Started game and opencode windows. If HostAddress is auto, each script prints the Tailscale URL it selected."
