# ⚡ AirClip (v1.0.0 Final Release)

**AirClip** is a lightweight, open-source, local-first Universal Clipboard engine. It seamlessly synchronizes text, links, and images between **Windows PC and iPhone / iPad / Mac / Linux** over your local Wi-Fi with zero cloud dependencies, zero account logins, and zero privacy exposure.

---

## 📖 The Story & Why AirClip Was Built

### The Problem
If you use a Windows PC alongside an iPhone, copying and pasting content between devices is notoriously frustrating:
- **Apple's Universal Clipboard** only works between Apple devices (Mac ↔ iPhone/iPad).
- **Cloud Relay Apps** (Pushbullet, WhatsApp self-messages, email drafts) require third-party servers, account sign-ins, internet access, and multiple taps.
- **File Transfer Utilities** (LocalSend, KDE Connect) are great for large files, but overkill for quickly pasting text or a screenshot copied on your PC to your phone.

### The Solution
AirClip runs silently in the background on your PC as a lightweight Flask + PyWebView engine. Coupled with native **iOS Shortcuts** assigned to iPhone **Back Tap** (double-tap the back of your phone) or the **Action Button**, copying content between Windows and iOS becomes as effortless as:
1. **PC → iPhone**: Copy anything on PC (`Ctrl+C` text or `Win+Shift+S` screenshot) ➔ Tap **Get Shortcut** on iPhone ➔ Content is on your iPhone clipboard!
2. **iPhone → PC**: Copy anything on iPhone ➔ Double-tap back of iPhone ➔ Content is pasted on your PC clipboard!

---

## ✨ Key Features

- 🔄 **Bidirectional Sync**: Instant text & screenshot synchronization between Windows PC and iOS/Mac/Linux.
- 🖼️ **Image & Screenshot Support**: Captures Snipping Tool (`Win+Shift+S`), PrintScreen, browser image copies, and image files copied in Windows File Explorer.
- 🎨 **Apple Liquid Glass Dashboard**: Embedded dark HSL glassmorphism UI built with PyWebView (EdgeChromium WebView2).
- 🚀 **Windows Startup Integration**: Toggle "Open AirClip on PC Startup" directly from the dashboard UI (persisted to Windows Registry).
- ⏸️ **Pause & Resume Controls**: One-click toggle pill to pause/resume server sync on demand.
- 🔄 **One-Click Server Reset**: Restart button clears rate limit maps and resets internal state with spinning visual feedback.
- 📜 **Live Activity Feed**: Real-time activity log displaying `SEND`, `RECV`, `LOCAL`, `WARN`, `ERROR`, and `INFO` events with persistent history memory.
- 📡 **mDNS Auto-Discovery**: Broadcasts `_clipsync._tcp.local` so devices on your Wi-Fi auto-discover your host.
- 🔑 **Token-Free Local Wi-Fi Simplicity**: Zero auth token setup required for home networks.

---

## 🛠️ Architecture & How Issues Were Resolved

During development, several technical challenges were solved to achieve zero-friction local syncing:

1. **PyWebView Origin & Relative API Fetching**:
   - *Problem*: Loading dashboard HTML from an in-memory string caused `fetch('/api/logs')` calls to fail due to `about:blank` base origin.
   - *Solution*: AirClip blocks startup until Flask accepts local connections (`_wait_for_flask()`) and loads `webview.create_window(url="http://127.0.0.1:5000/")`, enabling full relative REST API capabilities.

2. **Image Hashing vs. PIL Encoding Loops**:
   - *Problem*: Comparing raw PNG byte streams caused repeated log loops and blocked text paste because PIL encoder metadata varies between calls.
   - *Solution*: Implemented raw pixel MD5 hashing (`hashlib.md5(img.tobytes()) + f"_{img.size}_{img.mode}"`). This detects image updates in `0.001ms` without CPU re-encoding overhead.

3. **iOS Shortcuts Image Parsing**:
   - *Problem*: iOS Shortcuts struggled to parse binary image streams from `/get`.
   - *Solution*: Added explicit HTTP headers to `/get` image responses: `Content-Type: image/png`, `Content-Disposition: inline; filename="clipboard.png"`, and `Content-Length`.

4. **Windows Explorer File Copy Support**:
   - *Problem*: Copying image files in Windows Explorer returns a list of file paths rather than a bitmap object.
   - *Solution*: Added `isinstance(img, list)` detection in `monitor_pc_clipboard()` to load and convert Explorer image files automatically.

---

## 📱 1-Click iOS Shortcuts Setup

- 📤 **Send Shortcut (iPhone → PC)**: [Import Send Shortcut](https://www.icloud.com/shortcuts/09cb23ded9f84cd1a0eeea91450c5a49)
- 📥 **Get Shortcut (PC → iPhone)**: [Import Get Shortcut](https://www.icloud.com/shortcuts/bc67f1f38b4c4be8bf1d33e50c5191c1)

### 💡 Pro Tip: iPhone Back Tap Automation
1. Open iPhone **Settings > Accessibility > Touch > Back Tap**.
2. Assign **Double Tap** to **Send Shortcut** (iPhone → PC).
3. Assign **Triple Tap** to **Get Shortcut** (PC → iPhone).
4. Now double-tapping the back of your phone sends copied text/images to PC instantly!

---

## 🚀 Installation & Downloads

### Standalone Executable (Windows)
1. Download **`dist/AirClip.exe`** (38.7 MB single-file standalone executable).
2. Double-click `AirClip.exe` to run. No installation or Python setup needed!

### Running from Source (Cross-Platform)
```bash
git clone https://github.com/Arif-ai/airclip.git
cd airclip
pip install -r requirements.txt
python main.py
```

---

## 🏁 Final Release & Maintenance Notice

> [!NOTE]
> **This project has reached its final v1.0.0 release version.**
> AirClip fulfills 100% of my requirements and functions flawlessly for my daily cross-platform workflow. As a result, **I will no longer be actively developing, adding features, or taking feature requests for this repository**.
>
> The code is fully open-source under the MIT License. **Everyone in the community is welcome to fork this repository, adapt it, and build awesome new features upon it!**

---

## 📜 License

MIT License © 2026 Arif. Open-source software.
