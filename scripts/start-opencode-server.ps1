param(
    [int]$Port = 4096,
    [string]$HostAddress = "auto"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot

function Get-TailscaleExe {
    $command = Get-Command tailscale -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $candidates = @(
        "C:\Program Files\Tailscale\tailscale.exe",
        "C:\Program Files (x86)\Tailscale\tailscale.exe"
    )

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }

    return $null
}

function Get-BindAddress {
    param([string]$RequestedAddress)

    if ($RequestedAddress -ne "auto") {
        return $RequestedAddress
    }

    $tailscale = Get-TailscaleExe
    if ($tailscale) {
        $ip = & $tailscale ip -4 2>$null | Select-Object -First 1
        if ($LASTEXITCODE -eq 0 -and $ip) {
            return $ip.Trim()
        }
    }

    return "127.0.0.1"
}

Set-Location -LiteralPath $ProjectRoot

$BindAddress = Get-BindAddress -RequestedAddress $HostAddress

"Starting opencode server at http://$BindAddress`:$Port"
opencode serve --hostname $BindAddress --port $Port
