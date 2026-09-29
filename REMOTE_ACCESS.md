# Remote Access Over Tailscale

This project includes PowerShell launchers for two remote sessions:

- Game server: Flask on port `5000`
- OpenCode server: `opencode serve` on port `4096`

The scripts try to bind to this PC's Tailscale IPv4 address. If Tailscale is not installed or not logged in, they bind to `127.0.0.1` instead.

## One-Time Setup

1. Install Tailscale on this PC and the computer you will use away from home: <https://tailscale.com/download>
2. Log both devices into the same Tailscale account/tailnet.
3. On this PC, confirm Tailscale shows as connected.
4. If Windows Firewall prompts the first time the servers start, allow access on private networks.

If `tailscale` is not on `PATH`, the scripts also check the normal install locations under `C:\Program Files\Tailscale`.

## Start Both Sessions Before Leaving

From this project folder, run:

```powershell
.\scripts\start-remote-work.ps1
```

Two PowerShell windows will stay open:

- One for the game
- One for OpenCode

Each window prints the URL it is listening on, for example:

```text
http://100.x.y.z:5000
http://100.x.y.z:4096
```

Keep this PC awake and keep both windows open.

## Connect From Away

Open the game in a browser:

```text
http://<home-pc-tailscale-ip>:5000
```

Attach to the OpenCode server from another computer with OpenCode installed:

```powershell
opencode attach http://<home-pc-tailscale-ip>:4096
```

You can also try the OpenCode URL in a browser if you want the web interface:

```text
http://<home-pc-tailscale-ip>:4096
```

## Useful Options

Use different ports:

```powershell
.\scripts\start-remote-work.ps1 -GamePort 5050 -OpenCodePort 4097
```

Force a bind address:

```powershell
.\scripts\start-remote-work.ps1 -HostAddress 100.x.y.z
```

Local-only fallback:

```powershell
.\scripts\start-remote-work.ps1 -HostAddress 127.0.0.1
```
