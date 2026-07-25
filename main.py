"""
AirClip ⚡ — Cross-Platform Bidirectional Clipboard Synchronizer
License: MIT

Features:
- Bidirectional Clipboard Synchronization (PC ↔ iPhone / Android / Mac / Linux)
- Active / Deactivate Toggle (Pause/Resume server on demand)
- Loop & Overwrite Conflict Prevention (Separate PC & Mobile payload states)
- LAN-Only Security (Private IP Filter, Rate Limiting, 10MB Payload Cap)
- mDNS Auto-Discovery (_clipsync._tcp.local)
- Apple Liquid Glass Dashboard UI with dark QR code setup
- Windows Startup Registry Integration & Auto-Start Prompt
- Built-in Developer Secret (/secret)
"""

import io
import os
import sys
import time
import socket
import base64
import hashlib
import subprocess
import threading
import queue
import pyperclip
import webview
import qrcode
import winreg
from collections import defaultdict
from PIL import Image, ImageGrab
from flask import Flask, request, jsonify, send_file, Response, abort, make_response
from zeroconf import Zeroconf, ServiceInfo

# ─── PyInstaller / Frozen Path Resolution ─────────────────────────────────────
if getattr(sys, 'frozen', False):
    basedir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, basedir)
else:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.parsers import extract_text_from_incoming, extract_rtf_text

# ─── Configuration Constants ──────────────────────────────────────────────────
TEMP_DIR    = os.environ.get("TEMP", os.environ.get("TMPDIR", "/tmp"))
PORT        = 5000
MAX_PAYLOAD = 10 * 1024 * 1024   # 10 MB maximum request payload size
RATE_LIMIT  = 60                  # Maximum 60 requests/min per IP
PRIVATE_PREFIXES = (
    "192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.",
    "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.",
    "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.",
    "127.", "::1",
)

# ─── Developer Secret Payload ──────────────────────────────────────────────────
DEVELOPER_SECRET = {
    "engine":  "AirClip ⚡",
    "status":  "ok",
    "tagline": "Universal Local Clipboard Engine. Built for speed and absolute privacy.",
    "secret":  "⚡ Space and device boundaries mean nothing when clipboard is synchronized."
}

# ─── Windows Startup Registry Integration ──────────────────────────────────────
REG_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME    = "AirClip"

def is_startup_enabled() -> bool:
    """Check if AirClip is configured to open on Windows startup."""
    if sys.platform != "win32":
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except Exception:
        return False

def set_startup_enabled(enable: bool) -> bool:
    """Add or remove AirClip from the Windows startup registry."""
    if sys.platform != "win32":
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN_KEY, 0, winreg.KEY_WRITE) as key:
            if enable:
                exe_path = (
                    f'"{sys.executable}"'
                    if getattr(sys, 'frozen', False)
                    else f'"{sys.executable}" "{os.path.abspath(__file__)}"'
                )
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, exe_path)
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception as e:
        gui_log(f"Startup registry error: {e}", "ERROR")
        return False

# ─── Global State & Synchronization Controls ──────────────────────────────────
app          = Flask(__name__)
log_queue    = queue.Queue()
_LOG_HISTORY = []
_LOG_LOCK    = threading.Lock()

ui_window    = None
is_active    = True
_active_lock = threading.Lock()

_rate_map  = defaultdict(list)
_rate_lock = threading.Lock()

def get_local_ip() -> str:
    """Dynamically determine the host computer's primary LAN IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

LOCAL_IP  = get_local_ip()
start_now = time.time()

pc_state = {
    "type": "text",
    "text": "Server Ready",
    "image_bytes": None,
    "timestamp": start_now
}
last_pc_clipboard_text     = None
last_pc_clipboard_img_hash = None

# ─── Cross-Platform Clipboard Helper ──────────────────────────────────────────
def set_pc_image_clipboard(data: bytes) -> None:
    """Write raw PNG image bytes to host system clipboard."""
    try:
        tmp_path = os.path.join(TEMP_DIR, f"airclip_temp_{time.time_ns()}.png")
        Image.open(io.BytesIO(data)).save(tmp_path, "PNG")

        if sys.platform == "win32":
            ps_cmd = (
                f"Add-Type -AssemblyName System.Windows.Forms; "
                f"[System.Windows.Forms.Clipboard]::SetImage("
                f"[System.Drawing.Image]::FromFile('{tmp_path}'))"
            )
            subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True)
        elif sys.platform == "darwin":
            try:
                subprocess.run(["pngpaste", tmp_path], capture_output=True)
            except Exception:
                script = (
                    f'set theClipboard to (read (POSIX file "{tmp_path}") as «class PNGf»)\n'
                    f'set the clipboard to theClipboard'
                )
                subprocess.run(["osascript", "-e", script], capture_output=True)
        else:  # Linux
            try:
                subprocess.run(["xclip", "-selection", "clipboard", "-target", "image/png", "-i", tmp_path], capture_output=True)
            except Exception:
                subprocess.run(["wl-copy", "-t", "image/png", "<", tmp_path], shell=True, capture_output=True)
    except Exception as e:
        gui_log(f"Clipboard image write error: {e}", "ERROR")

# ─── Logging & CORS Security Middleware ──────────────────────────────────────
def gui_log(msg: str, tag: str = "INFO") -> None:
    """Queue formatted log messages for thread-safe UI rendering and maintain log history."""
    ts = time.strftime("%H:%M:%S")
    entry = (ts, msg, tag)
    log_queue.put(entry)
    with _LOG_LOCK:
        _LOG_HISTORY.append(entry)
        if len(_LOG_HISTORY) > 100:
            _LOG_HISTORY.pop(0)

def is_private_ip(ip: str) -> bool:
    """Check if requesting client IP is on private LAN."""
    return ip.startswith(PRIVATE_PREFIXES)

def is_rate_limited(ip: str) -> bool:
    """Enforce maximum request rates (60 req/min per IP)."""
    now = time.time()
    with _rate_lock:
        timestamps = _rate_map[ip]
        recent = [t for t in timestamps if now - t < 60]
        _rate_map[ip] = recent
        if len(recent) >= RATE_LIMIT:
            return True
        _rate_map[ip].append(now)
    return False

_BYPASS_ALL = {
    "/", "/api/logs", "/api/toggle-status", "/api/restart",
    "/api/startup-status", "/api/toggle-startup", "/secret",
    "/get", "/get-clip", "/send", "/send-clip"
}

def check_request():
    """Security filter executed before every API request."""
    clean_path = request.path.rstrip("/") or "/"
    if clean_path in _BYPASS_ALL:
        return

    if not is_active:
        gui_log("Blocked request: Server is DEACTIVATED", "WARN")
        abort(503, "AirClip server is currently deactivated by user")

    client_ip = request.remote_addr or ""
    if not is_private_ip(client_ip):
        gui_log(f"BLOCKED non-LAN request from {client_ip}", "WARN")
        abort(403, "Access restricted to local network")

    if is_rate_limited(client_ip):
        gui_log(f"RATE-LIMITED {client_ip}", "WARN")
        abort(429, "Too many requests")

    if request.content_length and request.content_length > MAX_PAYLOAD:
        abort(413, "Payload exceeds 10MB cap")

app.before_request(check_request)

@app.after_request
def add_cors_headers(response):
    """Ensure CORS headers allow access from all local clients."""
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

# ─── API Endpoints & Developer Secret ──────────────────────────────────────────
@app.route("/", methods=["GET"])
def index_web_dashboard():
    """Serve live Apple Liquid Glass Dashboard HTML."""
    return build_html()

@app.route("/secret", methods=["GET"])
def developer_secret():
    """Developer secret endpoint."""
    gui_log("⚡ Developer Secret unlocked!", "INFO")
    return jsonify(DEVELOPER_SECRET)

@app.route("/api/logs", methods=["GET"])
def get_logs_api():
    """Return queued activity log items (or recent history on request) to JS dashboard."""
    logs = []
    if request.args.get("history") == "1":
        with _LOG_LOCK:
            logs = list(_LOG_HISTORY)
    else:
        while not log_queue.empty():
            logs.append(log_queue.get())
    return jsonify({"logs": logs, "active": is_active, "startup": is_startup_enabled()})

@app.route("/api/toggle-status", methods=["POST"])
def toggle_status_api():
    """Toggle AirClip server active/deactivated state on demand."""
    global is_active
    with _active_lock:
        is_active = not is_active
        status_str = "ACTIVE" if is_active else "DEACTIVATED"
        gui_log(f"Server state changed to {status_str}", "INFO")
        return jsonify({"active": is_active})

@app.route("/api/restart", methods=["POST"])
def restart_server_api():
    """Reset AirClip server state and clear caches."""
    global pc_state, last_pc_clipboard_text, last_pc_clipboard_img_hash
    pc_state = {
        "type": "text",
        "text": "Server Ready",
        "image_bytes": None,
        "timestamp": time.time()
    }
    last_pc_clipboard_text     = None
    last_pc_clipboard_img_hash = None
    with _rate_lock:
        _rate_map.clear()
    with _LOG_LOCK:
        _LOG_HISTORY.clear()
    gui_log("Server state restarted cleanly", "INFO")
    return jsonify({"status": "ok"})

@app.route("/api/startup-status", methods=["GET"])
def api_startup_status():
    """Get Windows startup state."""
    return jsonify({"enabled": is_startup_enabled()})

@app.route("/api/toggle-startup", methods=["POST"])
def api_toggle_startup():
    """Toggle Windows startup configuration."""
    data   = request.get_json(silent=True) or {}
    enable = bool(data.get("enable", False))
    set_startup_enabled(enable)
    return jsonify({"enabled": is_startup_enabled()})

@app.route("/send",      methods=["POST", "OPTIONS"])
@app.route("/send-clip", methods=["POST", "OPTIONS"])
def send_iphone_clipboard():
    """Receive copied content (text or image) from mobile/remote client."""
    global last_pc_clipboard_text, last_pc_clipboard_img_hash
    if request.method == "OPTIONS":
        return Response(status=200)
    try:
        data = request.get_data() or b""
        if not data and request.form:
            data = "\n".join(request.form.values()).encode("utf-8")

        content_type = request.headers.get("Content-Type", "").lower()
        is_img_type  = ("image" in content_type or "octet-stream" in content_type)
        is_img_magic = data.startswith(b"\x89PNG") or data.startswith(b"\xff\xd8\xff") or data.startswith(b"GIF8")

        if data and (is_img_type or is_img_magic):
            set_pc_image_clipboard(data)
            img_hash = hashlib.md5(data).hexdigest()
            last_pc_clipboard_img_hash = img_hash
            try:
                last_pc_clipboard_text = pyperclip.paste()
            except Exception:
                pass
            pc_state.update({"type": "image", "image_bytes": data, "timestamp": time.time()})
            gui_log("iPhone → PC: Image received", "RECV")
            return jsonify({"status": "ok", "type": "image"}), 200

        elif data:
            text = extract_text_from_incoming(data)
            if text and '{"status"' not in text and not text.startswith("bplist00"):
                last_pc_clipboard_text = text  # Update tracker BEFORE copying to prevent echo loop
                try:
                    pyperclip.copy(text)
                except Exception as pe:
                    gui_log(f"Clipboard copy warning: {pe}", "WARN")
                pc_state.update({"type": "text", "text": text, "timestamp": time.time()})
                preview = text[:80] + ("..." if len(text) > 80 else "")
                gui_log(f"iPhone → PC: '{preview}'", "RECV")
                return jsonify({"status": "ok", "type": "text"}), 200
    except Exception as e:
        gui_log(f"SEND error: {e}", "ERROR")
    return Response(status=204)

@app.route("/get",      methods=["GET", "POST", "OPTIONS"])
@app.route("/get-clip", methods=["GET", "POST", "OPTIONS"])
def get_pc_clipboard():
    """Send current PC clipboard content (text or screenshot) to mobile/remote client."""
    if request.method == "OPTIONS":
        return Response(status=200)
    try:
        if pc_state["type"] == "text" and pc_state.get("text"):
            text    = pc_state["text"]
            preview = text[:80] + ("..." if len(text) > 80 else "")
            gui_log(f"PC → iPhone: '{preview}'", "SEND")
            return Response(text, status=200, content_type="text/plain; charset=utf-8")

        elif pc_state["type"] == "image" and pc_state.get("image_bytes"):
            gui_log("PC → iPhone: Image sent", "SEND")
            resp = make_response(pc_state["image_bytes"])
            resp.headers["Content-Type"]        = "image/png"
            resp.headers["Content-Disposition"] = 'inline; filename="clipboard.png"'
            resp.headers["Content-Length"]      = str(len(pc_state["image_bytes"]))
            return resp
    except Exception as e:
        gui_log(f"GET error: {e}", "ERROR")
    return Response(status=204)

# ─── Clipboard Background Monitoring ──────────────────────────────────────────
_IGNORE_PATTERNS = ("airclip_temp_", "iphone_clip_temp_", "bplist00")

def monitor_pc_clipboard():
    """Continuously monitor host system clipboard for changes."""
    global last_pc_clipboard_text, last_pc_clipboard_img_hash
    gui_log("Clipboard monitor active", "INFO")

    # Prime last_pc_clipboard_text on startup
    try:
        startup_text = pyperclip.paste()
        if startup_text:
            last_pc_clipboard_text = startup_text
    except Exception:
        pass

    while True:
        try:
            if is_active:
                # 1. Image Clipboard Inspection
                try:
                    img = ImageGrab.grabclipboard()

                    # Handle image files copied in Windows File Explorer
                    if isinstance(img, list):
                        for path in img:
                            if isinstance(path, str) and os.path.isfile(path):
                                ext = os.path.splitext(path)[1].lower()
                                if ext in ('.png', '.jpg', '.jpeg', '.bmp', '.webp', '.gif'):
                                    try:
                                        with Image.open(path) as loaded_img:
                                            img = loaded_img.convert("RGBA")
                                            break
                                    except Exception:
                                        pass

                    if isinstance(img, Image.Image):
                        pixel_hash = hashlib.md5(img.tobytes()).hexdigest() + f"_{img.size[0]}x{img.size[1]}_{img.mode}"
                        if pixel_hash != last_pc_clipboard_img_hash:
                            buf = io.BytesIO()
                            img.save(buf, format="PNG")
                            img_bytes = buf.getvalue()
                            last_pc_clipboard_img_hash = pixel_hash
                            try:
                                last_pc_clipboard_text = pyperclip.paste()
                            except Exception:
                                pass
                            pc_state.update({"type": "image", "image_bytes": img_bytes, "timestamp": time.time()})
                            gui_log("PC Copied: Image", "LOCAL")
                            time.sleep(0.8)
                            continue
                except Exception:
                    pass

                # 2. Text Clipboard Inspection
                try:
                    text = pyperclip.paste()
                    if text and text != last_pc_clipboard_text:
                        if any(p in text for p in _IGNORE_PATTERNS):
                            last_pc_clipboard_text = text
                            continue
                        if "{\\rtf1" in text:
                            text = extract_rtf_text(text)
                            try:
                                pyperclip.copy(text)
                            except Exception:
                                pass
                        last_pc_clipboard_text = text
                        pc_state.update({"type": "text", "text": text, "timestamp": time.time()})
                        preview = text[:80] + ("..." if len(text) > 80 else "")
                        gui_log(f"PC Copied: '{preview}'", "LOCAL")
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(0.5)

# ─── mDNS Zeroconf Auto-Discovery ─────────────────────────────────────────────
def register_mdns():
    """Broadcast AirClip on local network via mDNS."""
    try:
        zc   = Zeroconf()
        info = ServiceInfo(
            "_clipsync._tcp.local.",
            "AirClip._clipsync._tcp.local.",
            addresses=[socket.inet_aton(LOCAL_IP)],
            port=PORT,
            properties={"version": "1.0.0"},
            server="airclip.local.",
        )
        zc.register_service(info)
        gui_log("mDNS active (_clipsync._tcp.local)", "INFO")
    except Exception as e:
        gui_log(f"mDNS error: {e}", "WARN")

# ─── QR Code Generator Helper ──────────────────────────────────────────────────
def generate_qr_b64(url: str) -> str:
    """Generate base64-encoded PNG QR code."""
    try:
        qr = qrcode.QRCode(version=1, box_size=8, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#f0f0f5", back_color="#0a0a12")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""

# ─── Flask Server Thread ───────────────────────────────────────────────────────
def run_flask():
    app.run(host="0.0.0.0", port=PORT, threaded=True, debug=False, use_reloader=False)

# ─── Dashboard HTML Template ───────────────────────────────────────────────────
def build_html() -> str:
    SHORTCUT_SEND_URL = "https://www.icloud.com/shortcuts/09cb23ded9f84cd1a0eeea91450c5a49"
    SHORTCUT_GET_URL  = "https://www.icloud.com/shortcuts/bc67f1f38b4c4be8bf1d33e50c5191c1"

    send_qr  = generate_qr_b64(SHORTCUT_SEND_URL)
    get_qr   = generate_qr_b64(SHORTCUT_GET_URL)

    send_url = f"http://{LOCAL_IP}:{PORT}/send"
    get_url  = f"http://{LOCAL_IP}:{PORT}/get"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AirClip ⚡</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  :root {{
    --bg:      #08080f;
    --surface: rgba(255,255,255,0.05);
    --border:  rgba(255,255,255,0.10);
    --blue:    #3b82f6;
    --green:   #34d399;
    --amber:   #fbbf24;
    --red:     #f87171;
    --purple:  #a78bfa;
    --text:    #eff0f7;
    --muted:   rgba(239,240,247,0.45);
    --r:       14px;
    --r-sm:    10px;
  }}

  html, body {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: var(--bg);
    color: var(--text);
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    -webkit-font-smoothing: antialiased;
    user-select: none;
  }}

  body::before {{
    content: '';
    position: fixed; inset: 0; pointer-events: none; z-index: 0;
    background:
      radial-gradient(ellipse 80% 50% at 15% 10%, rgba(59,130,246,.13) 0%, transparent 60%),
      radial-gradient(ellipse 60% 40% at 85% 85%, rgba(167,139,250,.11) 0%, transparent 60%),
      radial-gradient(ellipse 50% 40% at 50% 115%, rgba(52,211,153,.07) 0%, transparent 55%);
  }}

  .shell {{
    position: relative; z-index: 1;
    display: flex; flex-direction: column;
    padding: 14px 16px; gap: 9px; flex: 1; min-height: 0;
  }}

  /* Header */
  .header {{
    display: flex; align-items: center; justify-content: space-between;
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--r); padding: 10px 14px;
    backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
  }}

  .brand {{ display: flex; align-items: center; gap: 9px; cursor: pointer; }}
  .brand-icon {{
    width: 34px; height: 34px; border-radius: 9px;
    background: linear-gradient(135deg, rgba(255,255,255,.15) 0%, rgba(255,255,255,.03) 100%);
    border: 1px solid rgba(255,255,255,.22);
    box-shadow: 0 6px 18px rgba(0,0,0,.45), inset 0 1px 1px rgba(255,255,255,.35);
    display: flex; align-items: center; justify-content: center;
    font-size: 15px; transition: transform .22s cubic-bezier(.16,1,.3,1);
  }}
  .brand-icon:hover {{ transform: scale(1.08); }}
  .brand-name {{ font-size: 15px; font-weight: 700; letter-spacing: -.3px; }}

  .controls {{ display: flex; gap: 6px; align-items: center; }}

  /* Pills */
  .pill {{
    display: flex; align-items: center; gap: 5px;
    border-radius: 100px; padding: 4px 11px;
    font-size: 10px; font-weight: 600; text-transform: uppercase;
    transition: all .2s ease; cursor: pointer; user-select: none;
    border: 1px solid transparent;
  }}
  .pill:active {{ transform: scale(.95); }}

  .pill-restart {{
    background: rgba(59,130,246,.10); border-color: rgba(59,130,246,.25); color: var(--blue);
  }}
  .pill-restart:hover {{ background: rgba(59,130,246,.22); transform: scale(1.04); }}
  .spinning {{ animation: spin .65s linear infinite; display: inline-block; }}

  .pill-active {{
    background: rgba(52,211,153,.10); border-color: rgba(52,211,153,.25); color: var(--green);
  }}
  .pill-active:hover  {{ background: rgba(52,211,153,.22); transform: scale(1.04); }}
  .pill-active.off    {{ background: rgba(248,113,113,.10); border-color: rgba(248,113,113,.25); color: var(--red); }}
  .pill-active.off:hover {{ background: rgba(248,113,113,.22); }}

  .status-dot {{
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--green); box-shadow: 0 0 7px var(--green);
    animation: pulse 2.2s ease-in-out infinite;
  }}
  .pill-active.off .status-dot {{
    background: var(--red); box-shadow: 0 0 7px var(--red); animation: none;
  }}

  .pill-mdns {{
    position: relative;
    background: rgba(167,139,250,.10); border-color: rgba(167,139,250,.25); color: var(--purple);
    cursor: help;
  }}
  .mdns-tip {{
    visibility: hidden; opacity: 0;
    position: absolute; top: calc(100% + 6px); right: 0;
    width: 220px; background: #0d0d1a;
    border: 1px solid rgba(167,139,250,.3); border-radius: 9px;
    padding: 9px 10px; font-size: 10px; text-transform: none;
    color: var(--text); line-height: 1.45; z-index: 200;
    box-shadow: 0 12px 28px rgba(0,0,0,.65);
    transition: opacity .18s ease;
  }}
  .pill-mdns:hover .mdns-tip {{ visibility: visible; opacity: 1; }}

  /* Startup bar */
  .startup-bar {{
    display: flex; align-items: center; justify-content: space-between;
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--r-sm); padding: 9px 13px;
  }}
  .startup-label {{ font-size: 11px; font-weight: 500; color: var(--text); }}

  .switch {{ position: relative; display: inline-block; width: 38px; height: 21px; }}
  .switch input {{ opacity: 0; width: 0; height: 0; }}
  .track {{
    position: absolute; inset: 0; cursor: pointer;
    background: rgba(255,255,255,.14); border-radius: 21px;
    border: 1px solid rgba(255,255,255,.18); transition: .28s;
  }}
  .track::before {{
    content: ''; position: absolute;
    width: 15px; height: 15px; border-radius: 50%;
    left: 2px; bottom: 2px;
    background: #fff; transition: .28s;
  }}
  input:checked + .track {{ background: var(--blue); border-color: var(--blue); }}
  input:checked + .track::before {{ transform: translateX(17px); }}

  /* URL cards */
  .urls-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
  .url-card {{
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--r-sm); padding: 9px 12px;
    transition: border-color .18s;
  }}
  .url-card:hover {{ border-color: rgba(255,255,255,.22); }}
  .url-label {{ font-size: 9px; font-weight: 600; text-transform: uppercase; letter-spacing: .8px; color: var(--muted); margin-bottom: 5px; }}
  .url-row {{ display: flex; align-items: center; gap: 6px; }}
  .url-text {{ font-size: 10.5px; font-family: 'SF Mono','Consolas',monospace; color: var(--blue); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .copy-btn {{
    padding: 3px 9px; border-radius: 6px;
    font-size: 9px; font-weight: 600; font-family: inherit; cursor: pointer;
    background: rgba(59,130,246,.15); color: var(--blue);
    border: 1px solid rgba(59,130,246,.28); transition: all .15s;
  }}
  .copy-btn:hover {{ background: rgba(59,130,246,.30); transform: scale(1.04); }}
  .copy-btn.ok {{ background: rgba(52,211,153,.15); color: var(--green); border-color: rgba(52,211,153,.28); }}

  /* QR Section */
  .qr-section {{
    display: flex; gap: 8px;
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--r-sm); padding: 8px;
  }}
  .qr-box {{
    flex: 1; display: flex; align-items: center; gap: 9px;
    background: rgba(10,10,18,.85); padding: 8px 10px; border-radius: 8px;
    border: 1px solid rgba(255,255,255,.08); transition: border-color .18s;
  }}
  .qr-box:hover {{ border-color: rgba(255,255,255,.18); }}
  .qr-img {{ width: 84px; height: 84px; border-radius: 8px; border: 1px solid rgba(255,255,255,.14); flex-shrink: 0; }}
  .qr-info {{ display: flex; flex-direction: column; gap: 3px; }}
  .qr-title {{ font-size: 10px; font-weight: 700; }}
  .qr-sub   {{ font-size: 9px; color: var(--muted); }}

  /* Activity Feed */
  .feed-wrap {{ display: flex; flex-direction: column; flex: 1; min-height: 0; }}
  .feed-head {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }}
  .feed-title {{ font-size: 9.5px; font-weight: 600; text-transform: uppercase; letter-spacing: .8px; color: var(--muted); }}
  .clear-btn {{
    padding: 3px 9px; border-radius: 5px;
    font-size: 9px; font-weight: 500; font-family: inherit; cursor: pointer;
    background: var(--surface); color: var(--muted);
    border: 1px solid var(--border); transition: all .15s;
  }}
  .clear-btn:hover {{ color: var(--text); background: rgba(255,255,255,.08); }}

  .feed {{
    flex: 1; overflow-y: auto; min-height: 0;
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--r-sm); padding: 7px;
    display: flex; flex-direction: column; gap: 4px;
  }}
  .feed::-webkit-scrollbar {{ width: 3px; }}
  .feed::-webkit-scrollbar-thumb {{ background: rgba(255,255,255,.12); border-radius: 3px; }}

  .log-row {{
    display: flex; align-items: flex-start; gap: 6px; padding: 5px 8px;
    border-radius: 6px; background: rgba(255,255,255,.025);
    border: 1px solid rgba(255,255,255,.05);
    font-size: 11px; line-height: 1.4;
    animation: fadeIn .18s ease both;
    transition: background .14s;
  }}
  .log-row:hover {{ background: rgba(255,255,255,.055); }}
  .log-ts  {{ font-size: 9px; color: var(--muted); font-family: 'Consolas',monospace; flex-shrink: 0; margin-top: 2px; }}
  .log-tag {{
    font-size: 8px; font-weight: 700; letter-spacing: .4px; text-transform: uppercase;
    flex-shrink: 0; width: 42px; text-align: center; padding: 2px 4px; border-radius: 4px; margin-top: 1px;
  }}
  .tag-SEND  {{ color: var(--blue);   background: rgba(59,130,246,.14);  border: 1px solid rgba(59,130,246,.22); }}
  .tag-RECV  {{ color: var(--green);  background: rgba(52,211,153,.12);  border: 1px solid rgba(52,211,153,.22); }}
  .tag-LOCAL {{ color: var(--amber);  background: rgba(251,191,36,.10);  border: 1px solid rgba(251,191,36,.22); }}
  .tag-WARN  {{ color: var(--red);    background: rgba(248,113,113,.10); border: 1px solid rgba(248,113,113,.22); }}
  .tag-ERROR {{ color: var(--red);    background: rgba(248,113,113,.15); border: 1px solid rgba(248,113,113,.28); }}
  .tag-INFO  {{ color: var(--purple); background: rgba(167,139,250,.10); border: 1px solid rgba(167,139,250,.22); }}
  .log-msg {{ color: var(--text); opacity: .92; word-break: break-word; flex: 1; }}

  .empty {{ flex: 1; display: flex; align-items: center; justify-content: center; color: var(--muted); font-size: 11px; }}

  /* Animations */
  @keyframes pulse  {{ 0%,100% {{ opacity:1; box-shadow:0 0 7px var(--green); }} 50% {{ opacity:.55; box-shadow:0 0 14px var(--green); }} }}
  @keyframes spin   {{ to {{ transform: rotate(360deg); }} }}
  @keyframes fadeIn {{ from {{ opacity:0; transform:translateX(-3px); }} to {{ opacity:1; transform:none; }} }}
</style>
</head>
<body>
<div class="shell">

  <!-- Header -->
  <div class="header">
    <div class="brand" onclick="revealSecret()" title="Click for secret">
      <div class="brand-icon">⚡</div>
      <span class="brand-name">AirClip</span>
    </div>
    <div class="controls">

      <!-- Restart -->
      <div class="pill pill-restart" id="restart-pill" onclick="restartServer()">
        <span id="restart-icon">🔄</span><span>Restart</span>
      </div>

      <!-- mDNS badge -->
      <div class="pill pill-mdns">
        <span>📡 mDNS</span>
        <div class="mdns-tip">
          <b>mDNS Auto-Discovery</b><br><br>
          Broadcasts AirClip on your local Wi-Fi (<code>_clipsync._tcp.local</code>) so iOS devices can find your PC automatically — no IP typing needed.
        </div>
      </div>

      <!-- Active/Pause toggle -->
      <div class="pill pill-active" id="active-pill" onclick="toggleStatus()">
        <div class="status-dot" id="status-dot"></div>
        <span id="active-label">Active</span>
      </div>

    </div>
  </div>

  <!-- Startup Toggle -->
  <div class="startup-bar">
    <span class="startup-label">🚀 &nbsp;Open AirClip automatically when PC starts</span>
    <label class="switch">
      <input type="checkbox" id="startup-chk" onchange="toggleStartup(this.checked)">
      <span class="track"></span>
    </label>
  </div>

  <!-- Endpoint URLs -->
  <div class="urls-grid">
    <div class="url-card">
      <div class="url-label">iPhone → PC &nbsp;(Send)</div>
      <div class="url-row">
        <span class="url-text">{send_url}</span>
        <button class="copy-btn" onclick="copyURL('{send_url}', this)">Copy</button>
      </div>
    </div>
    <div class="url-card">
      <div class="url-label">PC → iPhone &nbsp;(Get)</div>
      <div class="url-row">
        <span class="url-text">{get_url}</span>
        <button class="copy-btn" onclick="copyURL('{get_url}', this)">Copy</button>
      </div>
    </div>
  </div>

  <!-- QR Codes -->
  <div class="qr-section">
    <div class="qr-box">
      <img class="qr-img" src="data:image/png;base64,{send_qr}" alt="Send Shortcut QR">
      <div class="qr-info">
        <span class="qr-title">Send Shortcut</span>
        <span class="qr-sub">Scan to install on iOS</span>
      </div>
    </div>
    <div class="qr-box">
      <img class="qr-img" src="data:image/png;base64,{get_qr}" alt="Get Shortcut QR">
      <div class="qr-info">
        <span class="qr-title">Get Shortcut</span>
        <span class="qr-sub">Scan to install on iOS</span>
      </div>
    </div>
  </div>

  <!-- Live Activity Feed -->
  <div class="feed-wrap">
    <div class="feed-head">
      <span class="feed-title">Live Activity</span>
      <button class="clear-btn" onclick="clearFeed()">Clear</button>
    </div>
    <div class="feed" id="feed">
      <div class="empty" id="empty-msg">Waiting for clipboard events…</div>
    </div>
  </div>

</div><!-- /.shell -->

<script>
function revealSecret() {{
  fetch('/secret').then(r => r.json()).then(d => log('⚡ ' + d.secret, 'INFO'));
}}

function restartServer() {{
  const icon = document.getElementById('restart-icon');
  icon.classList.add('spinning');
  log('Restarting server engine…', 'INFO');
  fetch('/api/restart', {{ method: 'POST' }})
    .then(r => r.json())
    .then(() => {{
      setTimeout(() => {{
        icon.classList.remove('spinning');
        log('✅ Server restarted — state and rate limits cleared', 'INFO');
      }}, 600);
    }})
    .catch(() => {{ icon.classList.remove('spinning'); log('Restart failed', 'ERROR'); }});
}}

function toggleStatus() {{
  fetch('/api/toggle-status', {{ method: 'POST' }})
    .then(r => r.json())
    .then(d => {{
      applyActiveUI(d.active);
      log('Server ' + (d.active ? 'ACTIVE ✓' : 'PAUSED ⏸'), d.active ? 'INFO' : 'WARN');
    }});
}}

function applyActiveUI(active) {{
  const pill  = document.getElementById('active-pill');
  const label = document.getElementById('active-label');
  pill.className  = 'pill pill-active' + (active ? '' : ' off');
  label.textContent = active ? 'Active' : 'Paused';
}}

function toggleStartup(enabled) {{
  fetch('/api/toggle-startup', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ enable: enabled }})
  }})
  .then(r => r.json())
  .then(d => {{
    document.getElementById('startup-chk').checked = d.enabled;
    log('Launch on startup ' + (d.enabled ? 'ENABLED' : 'DISABLED'), 'INFO');
  }});
}}

function copyURL(url, btn) {{
  if (navigator.clipboard) {{
    navigator.clipboard.writeText(url).catch(() => fallbackCopy(url));
  }} else {{
    fallbackCopy(url);
  }}
  btn.textContent = 'Copied!'; btn.classList.add('ok');
  setTimeout(() => {{ btn.textContent = 'Copy'; btn.classList.remove('ok'); }}, 1600);
}}

function fallbackCopy(url) {{
  const ta = document.createElement('textarea');
  ta.value = url; document.body.appendChild(ta); ta.select();
  document.execCommand('copy'); document.body.removeChild(ta);
}}

function log(msg, tag, ts) {{
  const feed = document.getElementById('feed');
  const em   = document.getElementById('empty-msg');
  if (em) em.remove();
  const row = document.createElement('div');
  row.className = 'log-row';
  ts = ts || new Date().toTimeString().slice(0, 8);
  row.innerHTML =
    '<span class="log-ts">' + ts + '</span>' +
    '<span class="log-tag tag-' + tag + '">' + tag + '</span>' +
    '<span class="log-msg">' + esc(msg) + '</span>';
  feed.appendChild(row);
  feed.scrollTop = feed.scrollHeight;
  if (feed.children.length > 250) feed.removeChild(feed.firstChild);
}}

function clearFeed() {{
  document.getElementById('feed').innerHTML = '<div class="empty" id="empty-msg">Waiting for clipboard events…</div>';
}}

function esc(s) {{
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}}

setInterval(() => {{
  fetch('/api/logs')
    .then(r => r.json())
    .then(d => {{
      if (d.active !== undefined) applyActiveUI(d.active);
      if (d.startup !== undefined) document.getElementById('startup-chk').checked = d.startup;
      (d.logs || []).forEach(l => log(l[1], l[2], l[0]));
    }})
    .catch(() => {{}});
}}, 400);

window.addEventListener('load', () => {{
  log('AirClip engine online — port {PORT}', 'INFO');
  log('LAN-only · Rate limiter · 10 MB cap', 'INFO');

  fetch('/api/startup-status')
    .then(r => r.json())
    .then(d => {{ document.getElementById('startup-chk').checked = d.enabled; }});

  // Load full log history on load so feed is never blank
  fetch('/api/logs?history=1')
    .then(r => r.json())
    .then(d => {{
      if (d.active !== undefined) applyActiveUI(d.active);
      (d.logs || []).forEach(l => log(l[1], l[2], l[0]));
    }})
    .catch(() => {{}});
}});
</script>
</body>
</html>"""

# ─── Main Entry Point ─────────────────────────────────────────────────────────
def _wait_for_flask(timeout: float = 5.0) -> bool:
    """Block until Flask server is accepting connections."""
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=0.5)
            return True
        except Exception:
            time.sleep(0.1)
    return False

def main():
    global ui_window

    threading.Thread(target=run_flask,            daemon=True).start()
    threading.Thread(target=monitor_pc_clipboard, daemon=True).start()
    threading.Thread(target=register_mdns,        daemon=True).start()

    _wait_for_flask()

    window = webview.create_window(
        "AirClip ⚡",
        url=f"http://127.0.0.1:{PORT}/",
        width=640,
        height=620,
        min_size=(560, 520),
        background_color="#08080f",
        frameless=False,
    )
    ui_window = window
    webview.start(debug=False)

if __name__ == "__main__":
    main()
