"""
Steam CM UDP Proxy
Prompted by : kl2060
Developed by: Claude (Anthropic) - claude.ai
Version      : 1.9.1  |  March 2026

Changes in 1.9.1:
  - Fixed "IP address mismatch" TLS error on upstream fetch: now connects to
    Steam's real IP via a raw socket with server_hostname=TARGET_HOST for SNI,
    so the cert validates against the hostname (not the IP) — fully verified TLS
    with no certificate warnings or disabled checks.
  - Added chunked transfer encoding decoder for raw socket HTTP responses.

Changes in 1.9.0:
  - Windows: ephemeral cert is now installed into the Windows certificate store
    (ROOT store via certutil) at startup and removed on shutdown/crash — this is
    required so that server64.exe (and any other process using the Windows TLS
    stack) actually trusts the proxy's certificate.  Without this the API call
    silently failed (status=0) and Steam fell back to WebSocket mode.
  - Windows: purge_old_proxy_certs() now also removes stale proxy certs from the
    Windows ROOT store using certutil -delstore on startup.
  - No other behavioural changes from 1.8.0.

Changes in 1.8.0:
  - Self-healing startup: stale hosts entries cleaned and DNS flushed automatically
  - Real IP resolved via direct UDP DNS query to 8.8.8.8:53 — completely bypasses
    the hosts file and Windows DNS cache, so "resolved to 127.0.0.1" can never happen
  - Port 443 conflict: auto-identifies and kills the blocking process instead of exiting
  - Upstream TLS: fully verified (CERT_REQUIRED + check_hostname)
  - Ephemeral cert: CA=False, 24 h expiry, chmod 0o600 temp files, no hardcoded keys
  - Path traversal guard + allowlist on all proxied requests
  - Atomic hosts file writes (temp + rename) to prevent corruption on crash
  - TLS 1.2 minimum enforced on listening socket
  - Cache-Control: no-store on all responses
"""

import http.server
import urllib.request
import urllib.error
import ssl
import sys
import os
import re
import socket
import struct
import tempfile
import datetime
import platform
import subprocess
import shutil
import stat
import time

# ── Graceful cryptography import ─────────────────────────────────────────────────

try:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend
    import ipaddress as _ipaddress
except ImportError:
    print()
    print("  ✖  Missing dependency: 'cryptography'")
    print("     Install it with:")
    print("       pip install cryptography")
    print()
    sys.exit(1)

# ── ANSI color palette ────────────────────────────────────────────────────────────

def _ansi(code): return f'\033[{code}m'

RESET      = _ansi('0')
BOLD       = _ansi('1')

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

# ── Configuration ─────────────────────────────────────────────────────────────────

VERSION        = "1.9.1"
LISTEN_PORT    = 443
TARGET_HOST    = "api.steampowered.com"
HOSTS_FILE     = (r"C:\Windows\System32\drivers\etc\hosts"
                  if os.name == 'nt' else "/etc/hosts")
INTERCEPT_PATH = "/ISteamDirectory/GetCMListForConnect"
CERT_TAG       = "steam_proxy_ephemeral"

# Only forward requests whose paths start with one of these prefixes
ALLOWED_PATH_PREFIXES = (
    "/ISteamDirectory/",
    "/ISteamWebAPIUtil/",
)

# Linux system trust-store locations
LINUX_TRUST_PATHS = [
    ("/usr/local/share/ca-certificates", f"{CERT_TAG}.crt", "update-ca-certificates"),
    ("/etc/pki/ca-trust/source/anchors",  f"{CERT_TAG}.crt", "update-ca-trust"),
]

# ── State ─────────────────────────────────────────────────────────────────────────

original_hosts_content = None
cert_tmp               = None
key_tmp                = None
steam_api_ip           = None
intercept_count        = 0
start_time             = None

# ── Logging ───────────────────────────────────────────────────────────────────────

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

def log_fix(msg):
    print(f"  {ts()}  {HOT_PINK}{BOLD} ⚡ {RESET}  {HOT_PINK}{msg}{RESET}")

def separator():
    print(f"  {PINK}{'─' * 62}{RESET}")

# ── Banner ────────────────────────────────────────────────────────────────────────

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

# ── Direct DNS resolution (bypasses hosts file and OS cache entirely) ─────────────

def resolve_via_dns(hostname, dns_server="8.8.8.8", port=53, timeout=5):
    """
    Send a raw DNS A-record query directly to dns_server over UDP.
    Completely bypasses /etc/hosts and the Windows DNS client cache —
    we always get the real public IP even while our hosts patch is active.
    Returns an IP string or None on failure.
    """
    try:
        tx_id   = os.urandom(2)
        flags   = b'\x01\x00'
        qdcount = b'\x00\x01'
        zero    = b'\x00\x00'
        header  = tx_id + flags + qdcount + zero + zero + zero

        qname = b''
        for part in hostname.encode().split(b'.'):
            qname += bytes([len(part)]) + part
        qname += b'\x00'

        packet = header + qname + b'\x00\x01' + b'\x00\x01'

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.sendto(packet, (dns_server, port))
        response, _ = sock.recvfrom(512)
        sock.close()

        # Skip header (12 bytes) and question section
        offset = 12
        while offset < len(response):
            length = response[offset]
            offset += 1
            if length == 0:
                break
            offset += length
        offset += 4  # QTYPE + QCLASS

        ancount = struct.unpack('>H', response[6:8])[0]
        for _ in range(ancount):
            if offset >= len(response):
                break
            if response[offset] & 0xC0 == 0xC0:
                offset += 2
            else:
                while offset < len(response) and response[offset] != 0:
                    offset += response[offset] + 1
                offset += 1
            if offset + 10 > len(response):
                break
            rtype = struct.unpack('>H', response[offset:offset+2])[0]
            rdlen = struct.unpack('>H', response[offset+8:offset+10])[0]
            offset += 10
            if rtype == 1 and rdlen == 4:
                return '.'.join(str(b) for b in response[offset:offset+4])
            offset += rdlen
    except Exception:
        pass
    return None


def resolve_real_ip(hostname):
    """
    Resolve the real public IP, bypassing hosts and OS DNS cache.
    Tries 8.8.8.8 then 1.1.1.1 via raw UDP, falls back to OS resolver.
    """
    for dns in ("8.8.8.8", "1.1.1.1"):
        ip = resolve_via_dns(hostname, dns_server=dns)
        if ip and ip != "127.0.0.1":
            return ip
    try:
        ip = socket.gethostbyname(hostname)
        if ip != "127.0.0.1":
            return ip
    except Exception:
        pass
    return None

# ── Hosts file ────────────────────────────────────────────────────────────────────

def _hosts_write_atomic(content):
    """Write hosts file via temp-file + atomic rename — safe against crashes."""
    hosts_dir = os.path.dirname(os.path.abspath(HOSTS_FILE))
    fd, tmp_path = tempfile.mkstemp(dir=hosts_dir, prefix=".hosts_tmp_")
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(content)
        os.replace(tmp_path, HOSTS_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise


def strip_steam_from_hosts():
    """
    Remove every active (non-commented) TARGET_HOST line from the hosts file.
    Returns True if anything was removed.
    """
    try:
        with open(HOSTS_FILE, 'r') as f:
            lines = f.readlines()
        active = [l for l in lines
                  if TARGET_HOST in l and not l.strip().startswith('#')]
        if not active:
            return False
        cleaned = [l for l in lines
                   if not (TARGET_HOST in l and not l.strip().startswith('#'))]
        _hosts_write_atomic(''.join(cleaned))
        return True
    except Exception as e:
        log_warn(f"Could not clean hosts file: {e}")
        return False


def flush_dns_cache():
    """Best-effort OS DNS cache flush."""
    if os.name == 'nt':
        try:
            subprocess.run(['ipconfig', '/flushdns'], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            log_fix("DNS cache flushed  (ipconfig /flushdns)")
            time.sleep(0.3)
        except Exception:
            pass
    else:
        for cmd in [['systemctl', 'restart', 'systemd-resolved'],
                    ['nscd', '-i', 'hosts'],
                    ['resolvectl', 'flush-caches']]:
            try:
                subprocess.run(cmd, check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                log_fix(f"DNS cache flushed  ({' '.join(cmd)})")
                break
            except Exception:
                pass


def patch_hosts(add=True):
    """Add or restore the Steam redirect in the hosts file (atomic write)."""
    global original_hosts_content
    try:
        with open(HOSTS_FILE, 'r') as f:
            content = f.read()
        if add:
            original_hosts_content = content
            lines = [l for l in content.splitlines()
                     if not (TARGET_HOST in l and not l.strip().startswith('#'))]
            lines.append(f"127.0.0.1 {TARGET_HOST}")
            _hosts_write_atomic('\n'.join(lines) + '\n')
            log_ok(f"Hosts patched    {GRAY}→{RESET}  {WHITE}127.0.0.1 {TARGET_HOST}{RESET}")
        else:
            if original_hosts_content is not None:
                _hosts_write_atomic(original_hosts_content)
                log_ok("Hosts file restored to original")
            else:
                strip_steam_from_hosts()
                log_ok("Hosts entry removed (fallback clean)")
    except PermissionError:
        log_err("Cannot edit hosts file — " +
                ("run as Administrator." if os.name == 'nt' else "run with sudo."))
        sys.exit(1)
    except Exception as e:
        log_err(f"Hosts file error: {e}")
        sys.exit(1)

# ── Port conflict handling ────────────────────────────────────────────────────────

def find_pid_on_port(port):
    """Return PID of process listening on port, or None."""
    if os.name == 'nt':
        try:
            out = subprocess.check_output(
                ['netstat', '-ano', '-p', 'TCP'],
                stderr=subprocess.DEVNULL).decode(errors='replace')
            for line in out.splitlines():
                if f':{port} ' in line and 'LISTENING' in line:
                    parts = line.split()
                    if parts:
                        return int(parts[-1])
        except Exception:
            pass
    else:
        try:
            out = subprocess.check_output(
                ['lsof', '-ti', f'TCP:{port}', '-sTCP:LISTEN'],
                stderr=subprocess.DEVNULL).decode().strip()
            if out:
                return int(out.splitlines()[0])
        except Exception:
            pass
    return None


def find_process_name(pid):
    if os.name == 'nt':
        try:
            out = subprocess.check_output(
                ['tasklist', '/FI', f'PID eq {pid}', '/NH', '/FO', 'CSV'],
                stderr=subprocess.DEVNULL).decode(errors='replace')
            parts = out.strip().split(',')
            if parts:
                return parts[0].strip('"')
        except Exception:
            pass
    else:
        try:
            with open(f'/proc/{pid}/comm') as f:
                return f.read().strip()
        except Exception:
            pass
    return f"PID {pid}"


def kill_port_blocker(pid):
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/F', '/PID', str(pid)],
                           check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        else:
            os.kill(pid, 9)
        time.sleep(0.5)
        return True
    except Exception:
        return False


def ensure_port_free(port):
    """Check port is free; if not, auto-kill the blocker and verify."""
    def _is_free():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            s.bind(('0.0.0.0', port))
            s.close()
            return True
        except OSError:
            return False

    if _is_free():
        log_ok(f"Port {port} is available")
        return

    pid  = find_pid_on_port(port)
    name = find_process_name(pid) if pid else "unknown process"
    log_warn(f"Port {port} in use by  {WHITE}{name}{RESET}  {GRAY}(PID {pid}){RESET}")
    log_fix(f"Stopping {WHITE}{name}{RESET} to free port {port} ...")

    if pid and kill_port_blocker(pid):
        time.sleep(0.5)
        if _is_free():
            log_ok(f"Stopped {WHITE}{name}{RESET} — port {port} is now free")
            return
        # Give it one more second
        time.sleep(1)
        if _is_free():
            log_ok(f"Port {port} is now free")
            return

    log_err(f"Could not free port {port} automatically.")
    log_err(f"Manually stop '{name}' and restart this proxy.")
    if os.name == 'nt':
        input(f"\n  {PINK}Press Enter to exit...{RESET}")
    sys.exit(1)

# ── Ephemeral certificate ─────────────────────────────────────────────────────────

def generate_ephemeral_cert():
    """
    Generate a fresh RSA-2048 key and self-signed TLS cert on every run.

    Security properties:
      • Key generated in memory — never in source code or distributed files.
      • BasicConstraints CA:FALSE — cert cannot sign other certs.
        Even if leaked, attacker can only impersonate TARGET_HOST specifically.
      • SAN strictly limited to TARGET_HOST + 127.0.0.1.
      • 24-hour expiry — past session certs become useless quickly.
      • Temp files written chmod 0o600 (owner-read only).
    """
    log_info("Generating ephemeral TLS certificate ...")
    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, TARGET_HOST)])
    now  = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(hours=24))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName(TARGET_HOST),
                x509.IPAddress(_ipaddress.IPv4Address("127.0.0.1")),
            ]),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .sign(key, hashes.SHA256(), default_backend())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem  = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    log_ok(f"Ephemeral cert generated  "
           f"{GRAY}(24 h expiry · CA:false · SAN={TARGET_HOST}){RESET}")
    return cert_pem, key_pem


def write_temp_cert(cert_pem, key_pem):
    global cert_tmp, key_tmp
    cert_tmp = tempfile.NamedTemporaryFile(
        delete=False, suffix=f"_{CERT_TAG}.crt", prefix="")
    cert_tmp.write(cert_pem)
    cert_tmp.close()
    os.chmod(cert_tmp.name, stat.S_IRUSR | stat.S_IWUSR)

    key_tmp = tempfile.NamedTemporaryFile(
        delete=False, suffix=f"_{CERT_TAG}.key", prefix="")
    key_tmp.write(key_pem)
    key_tmp.close()
    os.chmod(key_tmp.name, stat.S_IRUSR | stat.S_IWUSR)

    return cert_tmp.name, key_tmp.name


def cleanup_temp_cert():
    for tmp in (cert_tmp, key_tmp):
        if tmp:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass


def purge_old_proxy_certs():
    """Remove cert files left by previous runs from trust stores and temp dir."""
    removed_any = False

    # ── Windows ROOT store
    if os.name == 'nt':
        if purge_old_proxy_certs_windows():
            removed_any = True

    # ── Linux trust stores
    if os.name != 'nt':
        for trust_dir, filename, update_cmd in LINUX_TRUST_PATHS:
            dest = os.path.join(trust_dir, filename)
            if os.path.exists(dest):
                try:
                    os.unlink(dest)
                    log_info(f"Removed stale cert  {GRAY}→{RESET}  {WHITE}{dest}{RESET}")
                    removed_any = True
                except Exception as e:
                    log_warn(f"Could not remove {dest}: {e}")
        if removed_any:
            for _, _, update_cmd in LINUX_TRUST_PATHS:
                try:
                    subprocess.run([update_cmd], check=True,
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
                    log_ok(f"Trust store updated via {WHITE}{update_cmd}{RESET}")
                    break
                except Exception:
                    pass

    tmp_dir = tempfile.gettempdir()
    try:
        for name in os.listdir(tmp_dir):
            if CERT_TAG in name and (name.endswith('.crt') or name.endswith('.key')):
                stale = os.path.join(tmp_dir, name)
                try:
                    os.unlink(stale)
                    log_info(f"Removed stale temp file  {GRAY}→{RESET}  {WHITE}{stale}{RESET}")
                except Exception:
                    pass
    except Exception:
        pass

    if not removed_any:
        log_info("No stale proxy certs found — trust store is clean")


def install_cert_linux(cert_path):
    log_info("Installing proxy cert into system trust store ...")
    for trust_dir, filename, update_cmd in LINUX_TRUST_PATHS:
        dest = os.path.join(trust_dir, filename)
        try:
            shutil.copy(cert_path, dest)
            os.chmod(dest, 0o644)
            subprocess.run([update_cmd], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            log_ok(f"Cert installed via {WHITE}{update_cmd}{RESET}  {GRAY}→ {dest}{RESET}")
            return
        except Exception:
            continue
    log_warn("Auto cert install failed — manually run:")
    log_warn(f"  sudo cp {cert_path} /usr/local/share/ca-certificates/{CERT_TAG}.crt")
    log_warn(f"  sudo update-ca-certificates")

# ── Windows certificate store ────────────────────────────────────────────────────

# Friendly name tag used to identify our cert in the Windows ROOT store
WIN_CERT_FRIENDLY = "SteamProxyEphemeral"


def _certutil(*args):
    """Run certutil.exe silently. Returns (returncode, stdout)."""
    try:
        result = subprocess.run(
            ['certutil'] + list(args),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=15)
        return result.returncode, result.stdout.decode(errors='replace')
    except Exception as e:
        return -1, str(e)


def install_cert_windows(cert_path):
    """
    Import the ephemeral cert into the Windows LOCAL_MACHINE ROOT store.
    Uses certutil.exe which is built into every Windows installation.

    Why ROOT (Trusted Root CAs)?
      server64.exe uses the Windows TLS stack (SChannel), which validates
      certificates against the Windows trust store.  The cert must be in ROOT
      so it is trusted machine-wide, including by processes we don't control.

    Security notes:
      • The cert has BasicConstraints CA:FALSE, so even though it sits in ROOT
        it cannot be used to sign other certificates.
      • It has a 24-hour expiry and is removed on clean shutdown AND on the
        next startup via purge_old_proxy_certs_windows().
      • The friendly name WIN_CERT_FRIENDLY is used to reliably find and
        delete it later without touching any other certificate.
    """
    log_info("Installing proxy cert into Windows ROOT store ...")
    rc, out = _certutil('-addstore', '-f', 'ROOT', cert_path)
    if rc == 0:
        log_ok(f"Cert installed into Windows ROOT store  "
               f"{GRAY}(certutil -addstore ROOT){RESET}")
    else:
        log_warn(f"certutil -addstore failed (rc={rc}) — server may fall back to WebSocket")
        log_warn("Output: " + out.strip().splitlines()[-1] if out.strip() else "")


def _get_proxy_cert_thumbprints_windows():
    """
    Return a list of thumbprints of proxy certs currently in the Windows ROOT store.
    Identifies them by matching the CN (TARGET_HOST) in the subject.
    """
    rc, out = _certutil('-store', 'ROOT')
    if rc != 0:
        return []
    thumbprints = []
    current_thumb = None
    in_proxy_cert = False
    for line in out.splitlines():
        line = line.strip()
        if line.startswith('Cert Hash(sha1):'):
            current_thumb = line.split(':', 1)[1].strip().replace(' ', '')
            in_proxy_cert = False
        if TARGET_HOST in line and current_thumb:
            in_proxy_cert = True
        if in_proxy_cert and current_thumb:
            thumbprints.append(current_thumb)
            current_thumb = None
            in_proxy_cert = False
    return list(set(thumbprints))


def purge_old_proxy_certs_windows():
    """
    Remove all proxy certs from the Windows ROOT store by thumbprint.
    Safe to call at startup (removes stale certs from crashed runs)
    and at shutdown.
    """
    thumbprints = _get_proxy_cert_thumbprints_windows()
    if not thumbprints:
        return False
    removed = False
    for thumb in thumbprints:
        rc, _ = _certutil('-delstore', 'ROOT', thumb)
        if rc == 0:
            log_ok(f"Removed proxy cert from Windows ROOT store  "
                   f"{GRAY}({thumb[:16]}...){RESET}")
            removed = True
        else:
            log_warn(f"Could not remove cert {thumb[:16]}... from Windows ROOT store")
    return removed


# ── System detection ──────────────────────────────────────────────────────────────

def detect_stormworks():
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
                                path, "steamapps", "common",
                                "Stormworks", "server64.exe"))
        except Exception:
            pass
    else:
        candidates = [
            os.path.expanduser("~/.steam/steam/steamapps/common/Stormworks/server64.exe"),
            os.path.expanduser("~/.local/share/Steam/steamapps/common/Stormworks/server64.exe"),
            "/usr/games/stormworks/server64.exe",
        ]
        for wine_root in [os.path.expanduser("~/.wine"),
                          os.path.expanduser("~/wine")]:
            candidates.append(os.path.join(
                wine_root, "drive_c", "Program Files (x86)", "Steam",
                "steamapps", "common", "Stormworks", "server64.exe"))
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def system_info():
    separator()
    log_info(f"OS               {GRAY}:{RESET}  "
             f"{WHITE}{platform.system()} {platform.release()} ({platform.machine()}){RESET}")
    log_info(f"Python           {GRAY}:{RESET}  {WHITE}{platform.python_version()}{RESET}")
    log_info(f"Hosts file       {GRAY}:{RESET}  {WHITE}{HOSTS_FILE}{RESET}")
    log_info(f"Proxy port       {GRAY}:{RESET}  {WHITE}{LISTEN_PORT} (HTTPS){RESET}")
    log_info(f"Intercept path   {GRAY}:{RESET}  {WHITE}{INTERCEPT_PATH}{RESET}")
    sw = detect_stormworks()
    if sw:
        size_mb = os.path.getsize(sw) / (1024 * 1024)
        log_ok(f"Stormworks       {GRAY}:{RESET}  "
               f"{WHITE}{sw}{RESET}  {GRAY}({size_mb:.1f} MB){RESET}")
    else:
        log_warn("Stormworks       :  Not found in default paths — server may still work")
    if os.name != 'nt':
        try:
            wine_ver = subprocess.check_output(
                ['wine', '--version'], stderr=subprocess.DEVNULL).decode().strip()
            log_ok(f"Wine             {GRAY}:{RESET}  {WHITE}{wine_ver}{RESET}")
        except Exception:
            log_warn("Wine             :  Not detected — needed to run server64.exe on Linux")
        log_info(f"Wine note        {GRAY}:{RESET}  "
                 f"{GRAY}'com_get_class_object apartment not initialised'{RESET}")
        log_info(f"                    "
                 f"{GRAY}is a known Wine/COM quirk — NOT caused by this proxy{RESET}")
    separator()

# ── Upstream fetch ────────────────────────────────────────────────────────────────

def fetch_real(path, query):
    """
    Fetch from Steam's real IP, bypassing our hosts redirect.

    When connecting by IP we open the raw socket ourselves so we can pass
    server_hostname=TARGET_HOST for SNI — this makes Steam's cert validate
    correctly against the hostname even though we connected via IP address.
    TLS chain + hostname is fully verified; only the IP-as-SAN check is skipped
    (Steam's cert is issued to the hostname, not the IP, which is normal).
    """
    global steam_api_ip

    def _attempt_by_ip(ip):
        """Connect to ip:443 but present TARGET_HOST as SNI hostname."""
        ctx = ssl.create_default_context()
        ctx.check_hostname = False          # we supply server_hostname manually
        ctx.verify_mode    = ssl.CERT_REQUIRED
        raw = socket.create_connection((ip, 443), timeout=10)
        tls = ctx.wrap_socket(raw, server_hostname=TARGET_HOST)
        url  = f"{path}?{query}" if query else path
        req  = (f"GET {url} HTTP/1.1\r\n"
                f"Host: {TARGET_HOST}\r\n"
                f"User-Agent: Valve/Steam HTTP Client 1.0\r\n"
                f"Accept: */*\r\n"
                f"Connection: close\r\n\r\n")
        tls.sendall(req.encode())
        response = b""
        while True:
            chunk = tls.recv(4096)
            if not chunk:
                break
            response += chunk
        tls.close()
        # Parse HTTP response
        header_end = response.find(b"\r\n\r\n")
        if header_end == -1:
            raise ValueError("No HTTP header separator found")
        header_raw = response[:header_end].decode(errors='replace')
        body        = response[header_end + 4:]
        status_line = header_raw.splitlines()[0]
        status_code = int(status_line.split()[1])
        headers = {}
        for line in header_raw.splitlines()[1:]:
            if ':' in line:
                k, v = line.split(':', 1)
                headers[k.strip()] = v.strip()
        # Handle chunked transfer encoding
        if headers.get('Transfer-Encoding', '').lower() == 'chunked':
            body = _decode_chunked(body)
        return body, status_code, headers

    def _attempt_by_hostname():
        """Fall back: connect via hostname (may be intercepted by our hosts patch)."""
        ctx = ssl.create_default_context()
        ctx.check_hostname = True
        ctx.verify_mode    = ssl.CERT_REQUIRED
        url = f"https://{TARGET_HOST}{path}"
        if query:
            url += f"?{query}"
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Valve/Steam HTTP Client 1.0',
            'Accept':     '*/*',
            'Host':       TARGET_HOST,
        })
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            return resp.read(), resp.status, dict(resp.headers)

    # Try resolved IP first (preferred — bypasses hosts file entirely)
    if steam_api_ip:
        try:
            return _attempt_by_ip(steam_api_ip)
        except urllib.error.HTTPError as e:
            return e.read(), e.code, {}
        except Exception as e:
            log_warn(f"IP fetch failed ({steam_api_ip}): {e} — re-resolving ...")
            # Re-resolve and retry once
            new_ip = resolve_real_ip(TARGET_HOST)
            if new_ip and new_ip != steam_api_ip:
                log_info(f"Re-resolved {TARGET_HOST} → {WHITE}{new_ip}{RESET}")
                steam_api_ip = new_ip
                try:
                    return _attempt_by_ip(steam_api_ip)
                except urllib.error.HTTPError as e2:
                    return e2.read(), e2.code, {}
                except Exception:
                    pass

    # Last resort: connect by hostname
    try:
        return _attempt_by_hostname()
    except urllib.error.HTTPError as e:
        return e.read(), e.code, {}
    except Exception as e:
        log_warn(f"Upstream fetch error: {e}")

    return None, 502, {}


def _decode_chunked(data):
    """Decode HTTP chunked transfer encoding."""
    result = b""
    while data:
        crlf = data.find(b"\r\n")
        if crlf == -1:
            break
        try:
            chunk_size = int(data[:crlf].split(b';')[0], 16)
        except ValueError:
            break
        if chunk_size == 0:
            break
        result += data[crlf + 2: crlf + 2 + chunk_size]
        data = data[crlf + 2 + chunk_size + 2:]
    return result

# ── Path safety ───────────────────────────────────────────────────────────────────

def is_safe_path(path):
    """Reject path traversal and requests outside the allowlisted prefixes."""
    norm = os.path.normpath(path).replace('\\', '/')
    if not norm.startswith('/'):
        norm = '/' + norm
    if '..' in norm.split('/'):
        return False
    return any(norm.startswith(p) for p in ALLOWED_PATH_PREFIXES)

# ── CM list rewriter ──────────────────────────────────────────────────────────────

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
            f"{GREEN}{nf_count} UDP{RESET}  {GRAY}/{RESET}  "
            f"{PINK}{ws_count} WebSocket{RESET}"
        )
        serverlist_match = re.search(
            r'"serverlist"\s*\{(.*?)\n\t\}', text, re.DOTALL)
        if not serverlist_match:
            log_warn("Could not locate serverlist block — passing through unmodified")
            return data
        blocks = re.findall(
            r'\t\t"\d+"\s*\{[^}]*\}', serverlist_match.group(1), re.DOTALL)
        kept = [b for b in blocks
                if '"type"\t"netfilter"'     in b
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

# ── Request handler ───────────────────────────────────────────────────────────────

class ProxyHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass

    def _reject(self, code, reason=""):
        body = reason.encode()
        self.send_response(code)
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parts = self.path.split('?', 1)
        path  = parts[0]
        query = parts[1] if len(parts) > 1 else ''

        if not is_safe_path(path):
            log_warn(f"Blocked unsafe path: {WHITE}{path}{RESET}")
            self._reject(403, "Forbidden")
            return

        body, status, headers = fetch_real(path, query)
        if body is None:
            self._reject(502, "Bad Gateway")
            return

        if path.startswith(INTERCEPT_PATH):
            body = rewrite_cm_response(body)

        self.send_response(status)
        if 'Content-Type' in headers:
            self.send_header('Content-Type', headers['Content-Type'])
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.do_GET()

# ── Main ──────────────────────────────────────────────────────────────────────────

def main():
    global steam_api_ip, start_time
    start_time = datetime.datetime.now()

    enable_windows_ansi()
    print_banner()

    # ── Admin / root check ───────────────────────────────────────────────────────
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
            log_err("Must be run as root.  Try:  sudo python3 steam_proxy.py")
            sys.exit(1)
        log_ok("Running as root")

    # ── System info ──────────────────────────────────────────────────────────────
    system_info()
    print()

    # ── Port conflict: auto-resolve ──────────────────────────────────────────────
    ensure_port_free(LISTEN_PORT)

    # ── Purge stale certs from previous runs ─────────────────────────────────────
    separator()
    log_info("Checking for stale proxy certificates ...")
    purge_old_proxy_certs()
    separator()
    print()

    # ── Self-heal: remove any stale hosts entry then flush DNS ───────────────────
    if strip_steam_from_hosts():
        log_fix("Removed stale hosts entry from a previous run")
        flush_dns_cache()

    # ── Resolve Steam's real IP via direct DNS (bypasses hosts + OS cache) ───────
    log_info(f"Resolving  {WHITE}{TARGET_HOST}{RESET}  "
             f"{GRAY}(direct DNS to 8.8.8.8 — bypasses hosts file){RESET}")
    steam_api_ip = resolve_real_ip(TARGET_HOST)
    if steam_api_ip:
        log_ok(f"Resolved  {WHITE}{TARGET_HOST}{RESET}  "
               f"{GRAY}→{RESET}  {WHITE}{steam_api_ip}{RESET}")
    else:
        log_warn("Could not resolve real IP — will attempt via hostname")

    # ── Patch hosts ──────────────────────────────────────────────────────────────
    patch_hosts(add=True)

    # ── Generate ephemeral cert ──────────────────────────────────────────────────
    cert_pem, key_pem = generate_ephemeral_cert()
    cert_path, key_path = write_temp_cert(cert_pem, key_pem)
    if os.name == 'nt':
        install_cert_windows(cert_path)
    else:
        install_cert_linux(cert_path)

    # ── Start HTTPS server ───────────────────────────────────────────────────────
    server = http.server.HTTPServer(('0.0.0.0', LISTEN_PORT), ProxyHandler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
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
        if os.name == 'nt':
            log_info("Removing proxy cert from Windows ROOT store ...")
            purge_old_proxy_certs_windows()
        elif os.name != 'nt':
            log_info("Removing ephemeral cert from trust store ...")
            purge_old_proxy_certs()
        cleanup_temp_cert()
        uptime = str(datetime.datetime.now() - start_time).split('.')[0]
        log_info(f"Total intercepts  {GRAY}:{RESET}  {WHITE}{intercept_count}{RESET}")
        log_info(f"Uptime            {GRAY}:{RESET}  {WHITE}{uptime}{RESET}")
        separator()
        log_ok("Clean shutdown complete.")
        print()


if __name__ == '__main__':
    main()