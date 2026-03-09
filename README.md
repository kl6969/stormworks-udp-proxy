# stormworks-udp-proxy
Forces Stormworks dedicated servers to connect to Steam via UDP instead of WebSockets, fixing server joining issue's. Intercepts Steam's CM list API and strips WebSocket entries. Works on Windows and Linux.


# stormworks-udp-proxy

Fixes Stormworks dedicated servers randomly disappearing from the server browser by forcing Steam to connect via **UDP** instead of WebSockets.

---

## The Problem

Steam's CM directory API (`ISteamDirectory/GetCMListForConnect`) returns a list of connection servers containing roughly 95–97% WebSocket entries and 3–5% UDP (Netfilter) entries. Steam picks a connection type by rolling a random number against that ratio on every boot.

When Steam picks **WebSockets**, the Stormworks dedicated server becomes bugged in the server browser and players cannot join — even though the server is running fine. When Steam picks **UDP**, everything works. Websockets makes the server randomly unjoinable with no obvious cause.

---

## The Fix

This proxy intercepts Steam's CM list API response and strips all WebSocket entries before Steam sees them. With zero WebSocket servers in the list, Steam always defaults to UDP — every boot, deterministically.

```
[Steam CM list API] ──► [steam_proxy] ──► strips WebSocket entries ──► [server64.exe]
                                                                         always UDP ✔
```

---

## Requirements

- Python 3.8+
- Must be run as **Administrator** (Windows) or **root / sudo** (Linux)
- The self-signed certificate must be trusted once (see setup below)

---

## Quick Start

### Windows

**1. Install the certificate (one time only)**
```cmd
certutil -addstore "Root" steam_proxy.crt
```

**2. Run the proxy as Administrator before starting your server**
```cmd
python steam_proxy.py
```
Or use the compiled `.exe` — right-click → Run as administrator.

**3. Start or restart `server64.exe`**

The proxy patches your hosts file automatically and restores it on exit (Ctrl+C).

---

### Linux (Debian/Ubuntu)

**1. Run with sudo**
```bash
sudo python3 steam_proxy.py
```

The proxy will automatically:
- Detect your OS and hosts file location
- Install the certificate into your system trust store via `update-ca-certificates`
- Patch `/etc/hosts` and restore it cleanly on exit

> **Cloud VM users (AWS, GCP, DigitalOcean, etc.):** If your VM uses cloud-init to manage `/etc/hosts`, the patch will be wiped on reboot. To make it permanent, disable cloud-init hosts management:
> ```bash
> echo "manage_etc_hosts: false" | sudo tee /etc/cloud/cloud.cfg.d/99-nohosts.cfg
> ```

---

## Building a standalone executable

### Windows `.exe`
```cmd
pip install pyinstaller
pyinstaller --onefile steam_proxy.py
```
Output: `dist/steam_proxy.exe`

### Linux binary
```bash
pip install pyinstaller
pyinstaller --onefile steam_proxy.py
```
Output: `dist/steam_proxy`

---

## How it works in detail

1. On startup, the proxy resolves `api.steampowered.com` to its real IP **before** patching the hosts file (prevents loopback)
2. Adds `127.0.0.1 api.steampowered.com` to the hosts file
3. Starts an HTTPS server on port 443 with a self-signed cert for `api.steampowered.com`
4. All Steam API requests from `server64.exe` hit the proxy instead of Steam's servers
5. The proxy forwards requests to Steam's real IP directly
6. For `/ISteamDirectory/GetCMListForConnect`, it strips all `"type" "websockets"` entries from the VDF response before returning it
7. Steam sees only Netfilter/UDP servers → always connects via UDP
8. On exit (Ctrl+C), the hosts file is restored to its original state

---

## Troubleshooting

**Proxy shows `Resolved api.steampowered.com → 127.0.0.1`**
The hosts file has a stale entry from a previous crashed run.
- Windows: `powershell -Command "(Get-Content C:\Windows\System32\drivers\etc\hosts) -notmatch 'steampowered' | Set-Content C:\Windows\System32\drivers\etc\hosts"`
- Linux: `sudo sed -i '/steampowered/d' /etc/hosts`

**`web API call failed (status = 502)` in the connection log**
The proxy isn't intercepting — check that it's running and the cert is installed.

**Port 443 already in use**
Something else is using port 443 (IIS, nginx, another proxy instance).
- Windows: check IIS / previous proxy instances
- Linux: `sudo lsof -i :443`

**`com_get_class_object apartment not initialised` on Linux**
This is a known Wine/COM quirk when running `server64.exe` under Wine. It is **not caused by this proxy** and can be safely ignored.

---

## Notes

- The proxy only intercepts one specific API endpoint — all other Steam API calls are forwarded transparently
- The hosts file is always restored on clean exit; if the proxy crashes run the stale entry cleanup command above
- Tested on Windows 10 and Debian 12 (cloud VM)

---

## Credits

Developed by [Claude](https://claude.ai) (Anthropic) for kl2060  
Stormworks by Geometa
Angezockt9980 for testing linux stablility / errors and providing information.
