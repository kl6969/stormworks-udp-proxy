"""
Steam CM UDP Proxy
Prompted by : kl2060
Developed by: Claude (Anthropic) - claude.ai
Version      : 1.6.0  |  March 2026
"""

import http.server
import urllib.request
import urllib.error
import ssl
import sys
import os
import re
import socket
import tempfile
import datetime
import platform
import subprocess
import shutil

# ── ANSI color palette (pink theme) ─────────────────────────────────────────────

def _ansi(code): return f'\033[{code}m'

RESET      = _ansi('0')
BOLD       = _ansi('1')
DIM        = _ansi('2')

HOT_PINK   = _ansi('38;5;198')
PINK       = _ansi('38;5;213')
LIGHT_PINK = _ansi('38;5;219')
MAGENTA    = _ansi('38;5;201')
WHITE      = _ansi('97')
GRAY       = _ansi('38;5;245')
GREEN      = _ansi('38;5;121')
YELLOW     = _ansi('38;5;228')
RED        = _ansi('38;5;210')

def enable_windows_ansi():
    if os.name == 'nt':
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass

# ── Configuration ────────────────────────────────────────────────────────────────

VERSION        = "1.6.0"
LISTEN_PORT    = 443
TARGET_HOST    = "api.steampowered.com"
HOSTS_FILE     = (r"C:\Windows\System32\drivers\etc\hosts"
                  if os.name == 'nt' else "/etc/hosts")
INTERCEPT_PATH = "/ISteamDirectory/GetCMListForConnect"

# ── Embedded TLS certificate ─────────────────────────────────────────────────────

CERT_PEM = b"""-----BEGIN CERTIFICATE-----
MIIDQDCCAiigAwIBAgIUF2OzxmSs3AtrvD6pqtR3S+abwpcwDQYJKoZIhvcNAQEL
BQAwHzEdMBsGA1UEAwwUYXBpLnN0ZWFtcG93ZXJlZC5jb20wHhcNMjYwMzA5MDUz
NjE5WhcNMzYwMzA2MDUzNjE5WjAfMR0wGwYDVQQDDBRhcGkuc3RlYW1wb3dlcmVk
LmNvbTCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBALqTNaj5GK7GphqO
asQ9UStkzlqsWV4NWAs3kl/7pH3RhZK3J3T/g6fALYjKw9m8qmuXnVAgrK5ektfr
VFvRz9/lgil5Wpk+1jbFetZl0K33+WjV/RLv7BOs75F4L4hEpj93k/AW46gNn8j9
3V6OKx2ssNw4jrYLa+yB3H6icJxfzs+JMfr8Y6ZeHwxxy4xzqYg2UfVXGus14vKF
eBkv4RyMworB8Bu/IVb75PmdgtkUDB7fToCjI9sxW23ZT85cRjnmkzq6StwH4j+0
M6RX+jZajnOvq2vV8cayLG7EnAJjPU8hpiR5c3vPkDajdGpYT1Yuj+mHizOkkpB8
Ds0ELwECAwEAAaN0MHIwHQYDVR0OBBYEFPqV7smBXrtzCQmT9Q9aPohxfUzTMB8G
A1UdIwQYMBaAFPqV7smBXrtzCQmT9Q9aPohxfUzTMA8GA1UdEwEB/wQFMAMBAf8w
HwYDVR0RBBgwFoIUYXBpLnN0ZWFtcG93ZXJlZC5jb20wDQYJKoZIhvcNAQELBQAD
ggEBALipSrdUOztwOyFR2NGoT5p/hdvWqqcfT1CbxUTVlpqkinJbUfm5PlrQNEt/
Jg74ApHjpv6QhRBwYJvFk0hGIXM/94SNrQGiTSy24m70gjKernNHKqBIXaoUTgwf
yq9Oc/8xGi7SIt2oRMtTMiTD1CwiwVgwqgdijeH2kSnT/2ZwZr7YU59FI8z/f0Ko
6HizD2DnQxuNuDEqH/eKlU727gR6cy+iPaxJN0ZaIe8ju9fECS2OfhQEWgAyE1GV
42Uo0QIRwxnZxmUVwNZIN7N22GEDCzZ+d1tgHltjWbSdFOwokXF3KurTGsltwuAU
7hOzRL8j9sT3YuMCWtWFfVbryjQ=
-----END CERTIFICATE-----"""

KEY_PEM = b"""-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC6kzWo+RiuxqYa
jmrEPVErZM5arFleDVgLN5Jf+6R90YWStyd0/4OnwC2IysPZvKprl51QIKyuXpLX
61Rb0c/f5YIpeVqZPtY2xXrWZdCt9/lo1f0S7+wTrO+ReC+IRKY/d5PwFuOoDZ/I
/d1ejisdrLDcOI62C2vsgdx+onCcX87PiTH6/GOmXh8MccuMc6mINlH1VxrrNeLy
hXgZL+EcjMKKwfAbvyFW++T5nYLZFAwe306AoyPbMVtt2U/OXEY55pM6ukrcB+I/
tDOkV/o2Wo5zr6tr1fHGsixuxJwCYz1PIaYkeXN7z5A2o3RqWE9WLo/ph4szpJKQ
fA7NBC8BAgMBAAECggEAA2t32BNKx8dV25ZBMDwkfPxhyOkwUShC9R+tY+t/ohvB
TEVlqIAXeG9uFjwLN3Y9FHBIvqN+rsqGfAUw/Gbd3c994Yc7KSRs9j+L5xqiJtIt
BOYpN5vksp6gnIS8sE170518PXIZ9aZcR6mZkWZfNXHJIxw5R25xqbneogaVtFQI
J/XM0uE3520nnRGccfYZ2+//4Qdz+DrAiij12fDqXKJryc4KgWlNHOrYRcG4fQLM
imCKYz6SZ5taVUSI12ewxXplR6jvh8AeDtj3T45BJKPKixp+PMHHv3kCaMCmPK+H
p8nIy8zoEkuE8xq9u8/MPl4Ba0Sqc7mPNgZeK6VmnQKBgQD5o/oJMh3BCmaRToFx
Oor8i4H+g0wrmnOT8rjxiLxSPsqNf+t8P2m3Pl0f0AQvSwIFs8zt34A6vhJuqLXf
TUyJWTOVOaHCe0LB3+8OvTLtNN4vW/uYQPi6IWdMm9Eqtp4skJ+tPeN1RsSkbsOv
8AQKhpuzHNN6eUFSRwpmf0id9QKBgQC/U/QBzNtFgIQy2wiyo6CTZ9KuHkXA+mbT
kr07hEzrnMCtuMXezKhqh3J0sPq4Jb6UvTV8AbzaDSLGEI4GcNuzdwN+SRTeiriz
RMDLf4FkZ2uaacv6Lhw+dvBvCEUDXZ0/WQa5AKYcNoPc483mkHxtsWsprKpSY8Fo
HulUwtJ5XQKBgEZ8DfTauZvvm9YbHGEj7mov2ZxK5g7JpSh4t886lDGEmRwqqgqC
vQ6IBTMeQJA51XBWu93N5R6w2/NynydVY+7DyNSxWQLYWpjy6UR4FxDyhGlKx1bN
wWyMUeZHeF9fAHoEu5DmkHpkaNEklQvv8LQoHX4M/YjvA4p/lGgsOAyhAoGAHSEx
E10XPVu1xPBoQJp9BjRWdUASqrD1Gt1Khlc8RtsU0t5A8g0Cz0VT/cQ8R/EnNQoh
rIGvORuq4bD/jqd8K7TBCWcjEEbanCCpodIF5z1/uoDFF5ARqMj/DkiaCUsld9Gc
Hmqk38LFDMp6PNJev2y1viCVxfl+JtYd/FO1K9kCgYEA+T58TsYEoLZlhLRfhD+h
0UELHFtuTVd40xVbuYITafCIqaXPv+/13m40+xH64ozvoNICfOIVdYW5EfkbOnb9
+dJiRRYSWtQQv1oeQiiAgkAVRyA0qSN86Q0VxMlMWQbjIfrlihcETgjWHB2tTqGa
gdeOz16ORNlTICVpfR6NZiQ=
-----END PRIVATE KEY-----"""

# ── State ────────────────────────────────────────────────────────────────────────

original_hosts_content = None
cert_file              = None
key_file               = None
steam_api_ip           = None
intercept_count        = 0
start_time             = None

# ── Logging ──────────────────────────────────────────────────────────────────────

def ts():
    return f"{GRAY}{datetime.datetime.now().strftime('%H:%M:%S')}{RESET}"

def log_ok(msg):
    print(f"  {ts()}  {GREEN}{BOLD} ✔ {RESET}  {LIGHT_PINK}{msg}{RESET}")

def log_info(msg):
    print(f"  {ts()}  {PINK}{BOLD} ℹ {RESET}  {LIGHT_PINK}{msg}{RESET}")

def log_warn(msg):
    print(f"  {ts()}  {YELLOW}{BOLD} ⚠ {RESET}  {YELLOW}{msg}{RESET}")

def log_err(msg):
    print(f"  {ts()}  {RED}{BOLD} ✖ {RESET}  {RED}{msg}{RESET}")

def log_intercept(msg):
    print(f"  {ts()}  {MAGENTA}{BOLD} ⟫ {RESET}  {MAGENTA}{msg}{RESET}")

def separator():
    print(f"  {PINK}{'─' * 62}{RESET}")

# ── Banner ───────────────────────────────────────────────────────────────────────

def print_banner():
    W = 62
    title_gap = W - len("  Steam CM UDP Proxy") - len(f"v{VERSION}  ")
    print()
    print(f"  {HOT_PINK}╔{'═'*W}╗{RESET}")
    print(f"  {HOT_PINK}║{RESET}  {BOLD}{WHITE}Steam CM UDP Proxy{RESET}"
          f"{' ' * title_gap}{GRAY}v{VERSION}{RESET}  {HOT_PINK}║{RESET}")
    print(f"  {HOT_PINK}║{RESET}  {PINK}Intercepts Steam's CM list and strips WebSocket entries,{RESET}    {HOT_PINK}║{RESET}")
    print(f"  {HOT_PINK}║{RESET}  {PINK}forcing dedicated servers to always connect via UDP.{RESET}        {HOT_PINK}║{RESET}")
    print(f"  {HOT_PINK}╠{'═'*W}╣{RESET}")
    print(f"  {HOT_PINK}║{RESET}  {WHITE}kl2060{RESET}  {GRAY}·  {PINK}Claude (Anthropic){RESET}  {GRAY}·  claude.ai{RESET}                 {HOT_PINK}║{RESET}")
    print(f"  {HOT_PINK}╚{'═'*W}╝{RESET}")
    print()

# ── System detection ─────────────────────────────────────────────────────────────

def detect_stormworks():
    """Auto-detect Stormworks server binary on this system."""
    candidates = []
    if os.name == 'nt':
        candidates = [
            r"C:\Program Files (x86)\Steam\steamapps\common\Stormworks\server64.exe",
            r"C:\Program Files\Steam\steamapps\common\Stormworks\server64.exe",
        ]
        try:
            vdf = r"C:\Program Files (x86)\Steam\steamapps\libraryfolders.vdf"
            if os.path.exists(vdf):
                with open(vdf, 'r') as f:
                    for line in f:
                        if '"path"' in line.lower():
                            path = line.split('"')[-2]
                            candidates.append(os.path.join(
                                path, "steamapps", "common", "Stormworks", "server64.exe"))
        except Exception:
            pass
    else:
        candidates = [
            os.path.expanduser("~/.steam/steam/steamapps/common/Stormworks/server64.exe"),
            os.path.expanduser("~/.local/share/Steam/steamapps/common/Stormworks/server64.exe"),
            "/usr/games/stormworks/server64.exe",
        ]
        for wine_root in [os.path.expanduser("~/.wine"), os.path.expanduser("~/wine")]:
            candidates.append(os.path.join(
                wine_root, "drive_c", "Program Files (x86)", "Steam",
                "steamapps", "common", "Stormworks", "server64.exe"))
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def system_info():
    """Print full system diagnostic info."""
    separator()
    log_info(f"OS               {GRAY}:{RESET}  {WHITE}{platform.system()} {platform.release()} ({platform.machine()}){RESET}")
    log_info(f"Python           {GRAY}:{RESET}  {WHITE}{platform.python_version()}{RESET}")
    log_info(f"Hosts file       {GRAY}:{RESET}  {WHITE}{HOSTS_FILE}{RESET}")
    log_info(f"Proxy port       {GRAY}:{RESET}  {WHITE}{LISTEN_PORT} (HTTPS){RESET}")
    log_info(f"Intercept path   {GRAY}:{RESET}  {WHITE}{INTERCEPT_PATH}{RESET}")

    sw = detect_stormworks()
    if sw:
        size_mb = os.path.getsize(sw) / (1024 * 1024)
        log_ok(f"Stormworks       {GRAY}:{RESET}  {WHITE}{sw}{RESET}  {GRAY}({size_mb:.1f} MB){RESET}")
    else:
        log_warn("Stormworks       :  Not found in default paths — server may still work")

    if os.name != 'nt':
        try:
            wine_ver = subprocess.check_output(
                ['wine', '--version'], stderr=subprocess.DEVNULL).decode().strip()
            log_ok(f"Wine             {GRAY}:{RESET}  {WHITE}{wine_ver}{RESET}")
        except Exception:
            log_warn("Wine             :  Not detected — needed to run server64.exe on Linux")
        log_info(f"Wine note        {GRAY}:{RESET}  {GRAY}'com_get_class_object apartment not initialised'{RESET}")
        log_info(f"                    {GRAY}is a known Wine/COM quirk — NOT caused by this proxy{RESET}")

    separator()

# ── Hosts file ───────────────────────────────────────────────────────────────────

def check_cloud_init():
    """Warn if cloud-init is managing /etc/hosts (common on cloud VMs)."""
    if os.name == 'nt':
        return
    cloud_cfg_paths = [
        "/etc/cloud/cloud.cfg",
        "/etc/cloud/cloud.cfg.d/",
    ]
    hosts_header = ""
    try:
        with open(HOSTS_FILE, 'r') as f:
            hosts_header = f.read(512)
    except Exception:
        return

    if 'manage_etc_hosts' in hosts_header:
        log_warn("cloud-init is managing /etc/hosts on this system!")
        log_warn("Our hosts patch may be wiped on reboot by cloud-init.")
        log_warn("To make it permanent, disable cloud-init hosts management:")
        log_warn("  Edit /etc/cloud/cloud.cfg and set:")
        log_warn("    manage_etc_hosts: false")
        log_warn("  OR add to /etc/cloud/cloud.cfg.d/99-nohosts.cfg:")
        log_warn("    manage_etc_hosts: false")
        log_warn("For now the proxy will still work this session.")
        print()


def validate_hosts_file():
    """Check hosts file for common issues and print a diagnostic summary."""
    try:
        with open(HOSTS_FILE, 'r') as f:
            lines = f.readlines()
    except Exception as e:
        log_warn(f"Could not read hosts file: {e}")
        return

    has_localhost  = any('127.0.0.1' in l and 'localhost' in l for l in lines)
    has_ipv6_local = any('::1' in l and 'localhost' in l for l in lines)
    has_steam      = any(TARGET_HOST in l for l in lines)
    cloud_managed  = any('manage_etc_hosts' in l for l in lines)

    log_info(f"Hosts: localhost   {GRAY}:{RESET}  {GREEN+'present'+RESET if has_localhost else YELLOW+'missing'+RESET}")
    log_info(f"Hosts: IPv6 local  {GRAY}:{RESET}  {GREEN+'present'+RESET if has_ipv6_local else GRAY+'absent (ok)'+RESET}")
    log_info(f"Hosts: cloud-init  {GRAY}:{RESET}  {YELLOW+'managed (see warning above)'+RESET if cloud_managed else GREEN+'not managed'+RESET}")
    if has_steam:
        steam_line = next((l.strip() for l in lines if TARGET_HOST in l), '')
        log_warn(f"Hosts: Steam entry already present: {WHITE}{steam_line}{RESET}")
    else:
        log_info(f"Hosts: Steam entry {GRAY}:{RESET}  {GRAY}not present (will be added){RESET}")


def patch_hosts(add=True):
    """
    Safely add or remove our Steam redirect from the hosts file.
    Only touches lines containing TARGET_HOST — never modifies anything else,
    preserving IPv6 entries, cloud-init headers, and all other records.
    """
    global original_hosts_content
    try:
        with open(HOSTS_FILE, 'r') as f:
            content = f.read()
        if add:
            original_hosts_content = content
            # Strip any existing Steam entry (stale or otherwise), then append ours
            lines = [l for l in content.splitlines()
                     if not (TARGET_HOST in l and not l.strip().startswith('#'))]
            lines.append(f"127.0.0.1 {TARGET_HOST}")
            with open(HOSTS_FILE, 'w') as f:
                f.write('\n'.join(lines) + '\n')
            log_ok(f"Hosts patched    {GRAY}→{RESET}  {WHITE}127.0.0.1 {TARGET_HOST}{RESET}")
        else:
            if original_hosts_content is not None:
                with open(HOSTS_FILE, 'w') as f:
                    f.write(original_hosts_content)
                log_ok("Hosts file restored to original")
            else:
                # Fallback: just strip our entry without full restore
                with open(HOSTS_FILE, 'r') as f:
                    current = f.read()
                lines = [l for l in current.splitlines()
                         if not (TARGET_HOST in l and not l.strip().startswith('#'))]
                with open(HOSTS_FILE, 'w') as f:
                    f.write('\n'.join(lines) + '\n')
                log_ok("Hosts entry removed (fallback clean)")
    except PermissionError:
        log_err("Cannot edit hosts file — " +
                ("run as Administrator." if os.name == 'nt' else "run with sudo."))
        sys.exit(1)
    except Exception as e:
        log_err(f"Hosts file error: {e}")
        sys.exit(1)

# ── Certificate ──────────────────────────────────────────────────────────────────

def write_temp_cert():
    global cert_file, key_file
    cert_file = tempfile.NamedTemporaryFile(delete=False, suffix='.crt')
    cert_file.write(CERT_PEM)
    cert_file.close()
    key_file = tempfile.NamedTemporaryFile(delete=False, suffix='.key')
    key_file.write(KEY_PEM)
    key_file.close()
    return cert_file.name, key_file.name

def cleanup_temp_cert():
    try:
        if cert_file: os.unlink(cert_file.name)
        if key_file:  os.unlink(key_file.name)
    except Exception:
        pass

def install_cert_linux(cert_path):
    """Try to install cert into Linux system trust store automatically."""
    log_info("Installing proxy cert into system trust store ...")
    # Debian/Ubuntu
    try:
        dest = "/usr/local/share/ca-certificates/steam_proxy.crt"
        shutil.copy(cert_path, dest)
        subprocess.run(['update-ca-certificates'], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log_ok("Cert installed via update-ca-certificates (Debian/Ubuntu)")
        return
    except Exception:
        pass
    # Fedora/RHEL/Arch
    try:
        dest = "/etc/pki/ca-trust/source/anchors/steam_proxy.crt"
        shutil.copy(cert_path, dest)
        subprocess.run(['update-ca-trust'], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log_ok("Cert installed via update-ca-trust (Fedora/RHEL/Arch)")
        return
    except Exception:
        pass
    log_warn("Auto cert install failed — manually run:")
    log_warn(f"  sudo cp {cert_path} /usr/local/share/ca-certificates/steam_proxy.crt")
    log_warn(f"  sudo update-ca-certificates")

# ── Upstream fetch ───────────────────────────────────────────────────────────────

def fetch_real(path, query):
    """Fetch from Steam's real IP, bypassing our hosts redirect."""
    global steam_api_ip

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE

    def _attempt(ip):
        url = f"https://{ip}{path}"
        if query:
            url += f"?{query}"
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Valve/Steam HTTP Client 1.0',
            'Accept':     '*/*',
            'Host':       TARGET_HOST,
        })
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            return resp.read(), resp.status, dict(resp.headers)

    try:
        return _attempt(steam_api_ip or TARGET_HOST)
    except urllib.error.HTTPError as e:
        return e.read(), e.code, {}
    except Exception:
        pass

    # IP may have rotated — re-resolve and retry
    try:
        new_ip = socket.getaddrinfo(TARGET_HOST, 443, proto=socket.IPPROTO_TCP)[0][4][0]
        if new_ip and new_ip != '127.0.0.1' and new_ip != steam_api_ip:
            log_info(f"Re-resolved {TARGET_HOST} → {WHITE}{new_ip}{RESET}{LIGHT_PINK} (IP rotated)")
            steam_api_ip = new_ip
        return _attempt(steam_api_ip or TARGET_HOST)
    except urllib.error.HTTPError as e:
        return e.read(), e.code, {}
    except Exception as e:
        if 'handshake operation timed out' not in str(e):
            log_warn(f"Upstream fetch error: {e}")

    return None, 502, {}

# ── CM list rewriter ─────────────────────────────────────────────────────────────

def rewrite_cm_response(data):
    """Strip WebSocket CM entries, keeping only Netfilter/UDP."""
    global intercept_count
    intercept_count += 1

    try:
        text = data.decode('utf-8', errors='replace')

        ws_count = len(re.findall(r'"type"\s+"websockets"', text))
        nf_count = len(re.findall(r'"type"\s+"netfilter"',  text))
        log_intercept(
            f"CM list received  {GRAY}—{RESET}  "
            f"{GREEN}{nf_count} UDP{RESET}  {GRAY}/{RESET}  {PINK}{ws_count} WebSocket{RESET}"
        )

        serverlist_match = re.search(r'"serverlist"\s*\{(.*?)\n\t\}', text, re.DOTALL)
        if not serverlist_match:
            log_warn("Could not locate serverlist block — passing through unmodified")
            return data

        blocks = re.findall(r'\t\t"\d+"\s*\{[^}]*\}', serverlist_match.group(1), re.DOTALL)
        kept   = [b for b in blocks if '"type"\t"netfilter"'     in b
                                    or '"type"\t"netfilter_udp"' in b]

        if not kept:
            log_warn("No netfilter servers found — passing through unmodified")
            return data

        renumbered = [
            re.sub(r'"\d+"(\s*\{)', f'"{i}"\\1', block, count=1)
            for i, block in enumerate(kept)
        ]
        new_text = text.replace(
            serverlist_match.group(1),
            '\n\t\t' + '\t\t'.join(renumbered) + '\n\t'
        )

        log_ok(
            f"Rewrote CM list  {GRAY}→{RESET}  "
            f"{GREEN}{BOLD}{len(kept)} UDP servers{RESET}  "
            f"{GRAY}(stripped {ws_count} WebSocket entries){RESET}"
        )
        return new_text.encode('utf-8')

    except Exception as e:
        log_err(f"Rewrite error: {e} — passing through unmodified")
        return data

# ── Request handler ──────────────────────────────────────────────────────────────

class ProxyHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        path, query = (self.path.split('?', 1) + [''])[:2]
        body, status, headers = fetch_real(path, query)
        if body is None:
            self.send_response(502)
            self.end_headers()
            return
        if path.startswith(INTERCEPT_PATH):
            body = rewrite_cm_response(body)
        self.send_response(status)
        if 'Content-Type' in headers:
            self.send_header('Content-Type', headers['Content-Type'])
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.do_GET()

# ── Main ─────────────────────────────────────────────────────────────────────────

def main():
    global steam_api_ip, start_time
    start_time = datetime.datetime.now()

    enable_windows_ansi()
    print_banner()

    # ── Admin / root check
    if os.name == 'nt':
        import ctypes
        if not ctypes.windll.shell32.IsUserAnAdmin():
            log_err("Must be run as Administrator.")
            log_err("Right-click the exe and choose 'Run as administrator'.")
            input(f"\n  {PINK}Press Enter to exit...{RESET}")
            sys.exit(1)
        log_ok("Running as Administrator")
    else:
        if os.geteuid() != 0:
            log_err("Must be run as root on Linux.")
            log_err("Try:  sudo python3 steam_proxy.py")
            sys.exit(1)
        log_ok("Running as root")

    # ── System info + auto-detection
    system_info()
    print()

    # ── Port check
    try:
        test = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        test.bind(('0.0.0.0', LISTEN_PORT))
        test.close()
    except OSError:
        log_err(f"Port {LISTEN_PORT} is already in use.")
        if os.name == 'nt':
            log_err("Stop any IIS, web server, or previous proxy instance.")
            input(f"\n  {PINK}Press Enter to exit...{RESET}")
        else:
            log_err("Try:  sudo lsof -i :443   to find what is using the port.")
        sys.exit(1)
    log_ok(f"Port {LISTEN_PORT} is available")

    # ── Cloud-init check (Linux cloud VMs)
    check_cloud_init()

    # ── Hosts file diagnostic
    validate_hosts_file()
    print()

    # ── Clean stale hosts entry from a previous crashed run
    try:
        with open(HOSTS_FILE, 'r') as f:
            stale = f.read()
        if TARGET_HOST in stale and '127.0.0.1' in stale:
            lines = [l for l in stale.splitlines()
                     if not (TARGET_HOST in l and not l.strip().startswith('#'))]
            with open(HOSTS_FILE, 'w') as f:
                f.write('\n'.join(lines) + '\n')
            log_info("Removed stale hosts entry from a previous run")
    except Exception:
        pass

    # ── Resolve Steam's real IP before patching hosts
    log_info(f"Resolving {WHITE}{TARGET_HOST}{RESET}{LIGHT_PINK} ...")
    try:
        steam_api_ip = socket.gethostbyname(TARGET_HOST)
        if steam_api_ip == '127.0.0.1':
            log_err("Resolved to 127.0.0.1 — hosts file is still dirty.")
            if os.name == 'nt':
                log_err("Run this in an admin cmd, then restart the proxy:")
                print(f"\n  {WHITE}powershell -Command \"(Get-Content C:\\Windows\\System32\\drivers\\etc\\hosts) -notmatch 'steampowered' | Set-Content C:\\Windows\\System32\\drivers\\etc\\hosts\"{RESET}\n")
                input(f"  {PINK}Press Enter to exit...{RESET}")
            else:
                log_err("Run this, then restart the proxy:")
                print(f"\n  {WHITE}sudo sed -i '/steampowered/d' /etc/hosts{RESET}\n")
            sys.exit(1)
        log_ok(f"Resolved  {WHITE}{TARGET_HOST}{RESET}  {GRAY}→{RESET}  {WHITE}{steam_api_ip}{RESET}")
    except Exception as e:
        log_warn(f"Resolution failed: {e}  (will attempt by hostname)")

    # ── Patch hosts
    patch_hosts(add=True)

    # ── Write cert, auto-install on Linux
    cert_path, key_path = write_temp_cert()
    if os.name != 'nt':
        install_cert_linux(cert_path)

    # ── Start HTTPS server
    server = http.server.HTTPServer(('0.0.0.0', LISTEN_PORT), ProxyHandler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)

    print()
    separator()
    log_ok(f"Proxy active on  {WHITE}0.0.0.0:{LISTEN_PORT}{RESET}  {GRAY}(HTTPS){RESET}")
    log_ok(f"Intercepting     {WHITE}{INTERCEPT_PATH}{RESET}")
    separator()
    print()
    print(f"  {HOT_PINK}►{RESET}  {WHITE}Start or restart your dedicated server now.{RESET}")
    print(f"  {HOT_PINK}►{RESET}  {GRAY}Press Ctrl+C to stop and restore your hosts file.{RESET}")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        print()
        separator()
        log_info("Shutting down...")
        server.shutdown()
        patch_hosts(add=False)
        cleanup_temp_cert()
        uptime = str(datetime.datetime.now() - start_time).split('.')[0]
        log_info(f"Total intercepts  {GRAY}:{RESET}  {WHITE}{intercept_count}{RESET}")
        log_info(f"Uptime            {GRAY}:{RESET}  {WHITE}{uptime}{RESET}")
        separator()
        log_ok("Clean shutdown complete.")
        print()

if __name__ == '__main__':
    main()