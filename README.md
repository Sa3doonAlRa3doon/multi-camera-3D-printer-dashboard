# Multi Camera Printer Dashboard

[![test](https://github.com/Sa3doonAlRa3doon/multi-camera-3D-printer-dashboard/actions/workflows/test.yml/badge.svg)](https://github.com/Sa3doonAlRa3doon/multi-camera-3D-printer-dashboard/actions/workflows/test.yml)

Multi Camera Printer Dashboard is a self-hosted, password-protected camera and 3D-printer workspace for Windows, Raspberry Pi OS, and desktop Linux. It displays up to six USB or network cameras at once, records screenshots/video/timelapses on the device viewing the page, and can embed a configurable printer web dashboard in a second tab.

Version: **1.2.0**

## Features

- Add, rename, edit, hide, remove, and switch between as many as six cameras.
- Adjustable one-to-four-column grid and per-camera fullscreen view.
- USB webcams plus compatible RTSP, HTTP/MJPEG, and OpenCV-readable stream URLs.
- Per-camera output resolution, FPS limit, 90°/180°/270° rotation, and horizontal/vertical flip.
- Independent capture workers with automatic reconnection, so one disconnected camera does not interrupt the others.
- Browser-side JPEG screenshots, WebM/MP4 video recordings, timed timelapses, and Fluidd/Moonraker layer timelapses. These files download to the laptop, phone, or tablet viewing the page—not to the Pi/server.
- Integrated 3D-printer dashboard tab with a full-page fallback when the printer UI blocks iframe embedding.
- Up to 10 private user accounts with administrator/viewer roles, editable usernames, enable/disable controls, password reset, and self-service password changes.
- Saved camera, printer, port, authentication, and startup settings.
- Authenticated LAN and Tailscale access using actual detected addresses.
- Interactive setup, manual launchers, Windows Task Scheduler startup, Linux systemd startup, and a data-preserving updater.

## Important security behavior

Camera credentials, stream URLs, printer URLs, account password hashes, logs, and runtime data are excluded from Git and stored under the installation folder. Never commit `config/`, `data/`, `logs/`, `backups/`, or `.env` files. Do not put a printer username or password inside its URL.

The built-in web server uses HTTP. Authentication protects the page and APIs, but HTTP does not encrypt traffic. Use only a trusted LAN or a private Tailscale network. See [SECURITY.md](SECURITY.md).

## Install on Windows

Requirements: Windows 10/11, Python 3.10 or newer, and a supported camera/stream.

1. Download this repository as a ZIP and extract it, or clone it:

   ```powershell
   git clone https://github.com/Sa3doonAlRa3doon/multi-camera-3D-printer-dashboard.git
   cd multi-camera-3D-printer-dashboard
   ```

2. Right-click `setup-windows.ps1` and run it with PowerShell, or run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1
   ```

3. Answer the folder, startup, port, login, and optional printer-dashboard questions.
4. If you selected manual startup, double-click `start-multi-camera-printer-dashboard.bat` inside the chosen installation folder.
5. Open one of the exact URLs printed by setup and sign in.

Windows automatic startup occurs at the current user's sign-in through Task Scheduler. It is not a pre-login system service and does not require an open terminal.

## Install on Raspberry Pi OS or Linux

Requirements: Raspberry Pi OS/Debian-compatible Linux, Python 3.10 or newer, and network access for dependency installation.

```bash
git clone https://github.com/Sa3doonAlRa3doon/multi-camera-3D-printer-dashboard.git
cd multi-camera-3D-printer-dashboard
chmod +x setup-linux.sh
./setup-linux.sh
```

The installer uses `apt` when available to install Python/OpenCV camera prerequisites. If automatic startup is selected, it registers `/etc/systemd/system/multi-camera-printer-dashboard.service`, enables it for normal system boot, starts it, and verifies that it remains active. The generated systemd paths are absolute and are not quoted in `WorkingDirectory=`.

If you select manual startup, run:

```bash
~/Documents/MultiCameraPrinterDashboard/start-multi-camera-printer-dashboard.sh
```

Use the actual folder printed by setup if you selected a custom location.

## Interactive setup

The installer first detects the real Documents folder. On Windows it supports redirected and OneDrive Documents folders. If normal detection fails, it searches likely user locations for a folder named `Documents`; if none is found, it asks for a full location instead of silently using another folder.

The default installation is:

- Windows: the detected `Documents\MultiCameraPrinterDashboard`
- Raspberry Pi OS/Linux: the detected `Documents/MultiCameraPrinterDashboard`

It then asks:

1. Where to store the application and all private data.
2. `Start Multi Camera Printer Dashboard automatically when this computer starts? [Y/N]`
3. Which port to use.
4. An administrator username and password.
5. An optional 3D-printer dashboard URL and display name. Leave the URL blank to configure it later.

Every launch prints the application/version, selected port, installation folder, local URL, detected LAN URLs, detected Tailscale URLs, autostart status, and log path. No address is hard-coded.

## Port selection rules

`8080` is the default **port**, not an IP address. Setup displays:

```text
Use port 8080?
[Y] Yes
[N] Enter a custom port
[A] Automatically select an available port
[D] Use the default port
```

Uppercase and lowercase input are accepted.

- `Y` or `D`: use 8080 if it is available; otherwise select automatically.
- `N`: require exactly four numeric digits from 1000 through 9999. Invalid input displays: `Please enter a 4-digit port number between 1000 and 9999, for example 8080, 9090, or 9000.`
- If a custom port is occupied, enter another or choose automatic selection.
- `A`: check, in exact order and without duplicates, `1010, 2020, 3030, 4040, 5050, 6060, 7070, 8080, 9090, 1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000`, then the remaining ports from 1000 through 9999.

Availability is checked on the real bind address. The server also handles a race where a port becomes occupied before startup. The selected port is saved; if it is unavailable later, startup clearly reports the problem, selects an available fallback, saves it, and prints the current URLs.

The preferred port can be changed later under **Settings → Network access**. Restart the program after changing it.

## Add and control cameras

1. Sign in and select **Add camera**, or open **Settings → Detect USB cameras**.
2. Give the camera a name.
3. Select USB, RTSP, or HTTP/MJPEG.
4. Enter a USB index/path such as `0` or `/dev/video0`, or enter a complete compatible stream URL.
5. For a network camera, enter credentials in the separate fields so they remain private and are not exposed in the browser.
6. Optionally set resolution, target FPS, rotation, and flip.
7. Save. The counter in Settings shows how many of the six available camera slots are used.

The server asks USB drivers for the selected size/FPS when supported, then applies output resizing and frame limiting. Network streams are decoded and converted to MJPEG for browser playback. Rotation happens after resizing; a 1280 × 720 source rotated 90° appears as 720 × 1280.

Compatibility depends on the camera, authentication scheme, codec, and OpenCV/FFmpeg backend. RTSP H.264 and MJPEG sources are commonly usable; H.265 support varies. Audio is not supported.

## User accounts

The first setup login is the initial administrator. Open **Settings → User accounts** to add, rename, enable, disable, reset, or remove accounts. The dashboard supports up to 10 accounts, so four separate logins can be created without sharing a password.

- **Administrator** accounts can manage cameras, printer URLs, startup, port, and accounts.
- **Viewer** accounts can view cameras/printer information and create screenshots, recordings, and timelapses on their own viewing device, but cannot change server settings.
- Every signed-in user can change their own password under **Settings → My password**.
- Usernames are unique without regard to uppercase/lowercase. Passwords require at least eight characters.
- The final enabled administrator cannot be disabled, demoted, or removed. An administrator cannot delete the account currently being used; sign in as another administrator first.

Upgrading from version 1.1.0 or earlier automatically converts the existing login into the first administrator without changing its username or password. Account records and password hashes remain in private `config/settings.json` and are never returned by the public repository.

## Screenshot, normal video, and timelapse

Each camera card offers:

- **Screenshot**: downloads a timestamped JPEG.
- **Record**: starts a continuous browser-side recording. Select **Stop & save** to download a WebM or MP4 file, depending on the viewing browser's supported encoder.
- **Timelapse**: captures one JPEG frame for every new printer layer by default, or uses a chosen time interval. Frames remain in browser memory until the print completes or **Stop & save** is selected, then a supported WebM/MP4 timelapse downloads.
- **Fullscreen**: expands one camera.

Timelapse defaults are under **Settings → Timelapse defaults**:

- Capture trigger: **Every printer layer** by default, or **Time interval** as a fallback.
- Timer fallback interval: 2 through 3600 seconds; default 10.
- Playback speed: 1 through 60 FPS; default 30.
- Maximum frames: 60 through 10,000; default 3,000.

Layer mode polls Moonraker once per second. It uses `print_stats.info.current_layer` when the firmware supplies it. If the firmware returns a null layer number but supplies `print_stats.z_pos`, the dashboard automatically captures once for each new maximum Z height; this is the compatibility path used by some Creality firmware. Duplicate values and paused states do not create frames. Completion, cancellation, or a print error automatically finishes and downloads any captured timelapse. Starting midway through a print captures the current layer/height and then each following increase.

Native layer numbers are more exact than the Z-height fallback, especially for unusual spiral/vase or Z-hop jobs. For OrcaSlicer, native layer statistics can be enabled by adding this to **Machine start G-code**:

```gcode
SET_PRINT_STATS_INFO TOTAL_LAYER=[total_layer_count]
```

Add this to **Layer change G-code**:

```gcode
SET_PRINT_STATS_INFO CURRENT_LAYER={layer_num + 1}
```

Other slicers need equivalent placeholders. Existing start/layer-change commands should be retained; add these lines rather than replacing the entire printer profile.

The viewing browser tab must remain open during any recording or timelapse. Longer timelapses use more laptop/phone memory. A leave-page warning appears while capture is active. Current Chrome, Edge, Chromium, or Firefox is recommended; browser MediaRecorder and canvas-capture support are required.

All capture files are generated by the viewing browser and use its normal Downloads behavior. They are not saved to the Raspberry Pi unless the Pi itself is the device running the browser.

## 3D-printer dashboard

Open **Settings → 3D printer dashboard**, enter a name, and enter the printer UI's complete local URL. For Creality/Fluidd installations that use port 4408, the shape is commonly:

```text
http://printer-ip:4408/#/
```

Select the **3D printer** tab to load it. **Reload** refreshes the embedded page, and **Open full page** opens the printer's own interface directly.

Layer timelapse first tries the saved dashboard origin at `/printer/objects/query?print_stats`, then automatically tries Moonraker on the same host at port `7125`. If the printer uses another address or port, enter its base URL under **Moonraker API URL** and select **Test layer connection**. The Raspberry Pi or computer running this application must be able to reach that API address. No printer IP address is hard-coded into the public repository.

Some printer dashboards send `X-Frame-Options` or Content Security Policy headers that prohibit embedding. The application cannot override the printer's browser security policy; if the frame is blank or reports that it refused to connect, use **Open full page**. Camera streams and capture continue independently.

The printer URL is deliberately not hard-coded in this public repository. It is saved under the selected installation's private `config/settings.json` and returned only after application authentication.

## LAN and Tailscale access

The server binds to `0.0.0.0`, so the same authenticated application can be reached through its detected LAN and Tailscale addresses. Setup and every launch print the actual URLs, for example `http://<detected-address>:<selected-port>`.

Tailscale must already be installed, signed in, connected, and permitted by your tailnet policy on the server and the device viewing the dashboard. This project does not install or enroll Tailscale.

The embedded printer page is loaded by the viewing browser, not relayed through this application. Layer status is different: the Multi Camera Printer Dashboard server queries Moonraker, so layer capture can work for a remote Tailscale viewer as long as the server can reach the printer. For the remote viewer to also see the embedded printer page, either:

- install/connect Tailscale on the printer when its platform supports it, then save its Tailscale address; or
- configure an existing Tailscale device as an approved subnet router for the printer's LAN and allow the route.

If neither is configured, the camera application can still work over Tailscale while the printer frame cannot reach its private LAN URL.

## Storage layout

Everything stays under the selected installation folder:

| Path | Contents |
| --- | --- |
| `app/` | Application code and browser interface |
| `.venv/` | Private Python environment |
| `config/settings.json` | Port, private account hashes/roles, printer URLs, and startup preference |
| `data/cameras.json` | Camera definitions and credentials |
| `logs/multi-camera-printer-dashboard.log` | Rotating runtime log |
| `backups/` | Automatic pre-upgrade code and private-data ZIP backups |

On Linux, private folders/files receive restrictive permissions. On Windows, setup applies current-user ACLs when available.

## Upgrade without losing data

From the selected installation folder:

Windows:

```powershell
.\update-multi-camera-printer-dashboard.bat
# or
.\.venv\Scripts\python.exe manage.py check-update
.\.venv\Scripts\python.exe manage.py update
```

Raspberry Pi OS/Linux:

```bash
./update-multi-camera-printer-dashboard.sh
# or
./.venv/bin/python manage.py check-update
./.venv/bin/python manage.py update
```

The updater checks this repository's `VERSION`, downloads and validates the release source, creates backups, replaces program files only, installs dependencies, and restarts the previous run mode. It never replaces `config/`, `data/`, or `logs/`. If dependency installation fails, it restores the previous code. Camera settings, credentials, printer URLs, port, and every account therefore survive upgrades.

Maintainers must increase `VERSION` using `major.minor.patch` and publish the matching code in the same commit.

## Manual control and automatic startup

Startup mode is changeable at any time under **Settings → Automatic startup**. On Pi/Linux, an administrative terminal command is displayed when systemd permission is required.

Run these from the installation folder:

Windows:

```powershell
.\.venv\Scripts\python.exe manage.py status
.\.venv\Scripts\python.exe manage.py start
.\.venv\Scripts\python.exe manage.py stop
.\.venv\Scripts\python.exe manage.py enable-autostart
.\.venv\Scripts\python.exe manage.py disable-autostart
.\.venv\Scripts\python.exe manage.py remove-autostart
```

Raspberry Pi OS/Linux:

```bash
./.venv/bin/python manage.py status
./.venv/bin/python manage.py start
./.venv/bin/python manage.py stop
./.venv/bin/python manage.py enable-autostart
./.venv/bin/python manage.py disable-autostart
./.venv/bin/python manage.py remove-autostart
```

`enable-autostart` regenerates the current registration. On Linux it reloads systemd, enables the service, explicitly restarts it, and verifies that it stays active. Windows uses the current user's sign-in task. Linux uses a system service that starts at boot. `disable-autostart --keep-running` disables the next automatic launch without stopping the current Linux service. `remove-autostart` removes the Windows task or Linux unit.

## Troubleshooting

- **Camera disconnected:** verify its index/path/URL and credentials; the card reports retry state while the worker reconnects.
- **Camera busy:** close video-call or camera applications that may already own the USB device.
- **High Pi CPU/network use:** lower camera output resolution/FPS, use network-camera substreams, or show fewer feeds. Each active stream is decoded and encoded as MJPEG.
- **Printer frame blank/refused:** use **Open full page**; the printer UI probably blocks iframe embedding.
- **Layer timelapse cannot connect:** open **Settings → 3D printer dashboard**, leave Moonraker API URL blank for automatic dashboard/port-7125 detection, then select **Test layer connection**. If needed, enter the printer's explicit Moonraker base URL such as `http://printer-address:7125/`.
- **Layer number is null:** version 1.2.0 automatically uses increasing Z height. Native slicer `SET_PRINT_STATS_INFO` layer commands are still recommended for exact layer counting.
- **Printer unavailable over Tailscale:** make its address reachable through Tailscale or an approved subnet route; the application does not proxy the printer.
- **No Tailscale URL:** connect Tailscale on the server and viewing device, then restart the application.
- **Recording/timelapse unavailable:** use a current Chrome, Edge, Chromium, Firefox, or Safari browser with MediaRecorder/canvas capture support. The app detects WebM/MP4 support, waits for final encoder data, rejects empty files, and shows the downloaded file size.
- **Saved port changed:** the original port was occupied at launch; read the startup output/log for the selected fallback.
- **Linux autostart inactive:** run `./.venv/bin/python manage.py enable-autostart`, then inspect `sudo systemctl status multi-camera-printer-dashboard.service --no-pager --full` and `sudo journalctl -u multi-camera-printer-dashboard.service -n 100 --no-pager`.

### Repair autostart after upgrading from 1.0.0

Run this from the installed application folder. It preserves cameras, login details, the printer URL, port, recordings, and logs:

```bash
./.venv/bin/python manage.py update
./.venv/bin/python manage.py enable-autostart
./.venv/bin/python manage.py status
```

The final status must say `enabled and running (system boot)`. Version 1.0.1 changed the Linux repair sequence to explicitly reload, enable, restart, and verify the service instead of relying on `systemctl enable --now` to refresh an existing unit.

## Uninstall

Uninstalling removes the application, camera/printer/login settings, logs, backups, and private environment. Browser-downloaded screenshots and videos remain on the viewing device. Tailscale and printer software are separate and are not removed.

Back up `config/` and `data/` privately first if you want to preserve settings.

Raspberry Pi OS/Linux:

```bash
cd ~/Documents/MultiCameraPrinterDashboard
./.venv/bin/python manage.py remove-autostart
./.venv/bin/python manage.py stop
```

Then close terminals inside the folder and delete `MultiCameraPrinterDashboard` using the desktop file manager/Trash. On a terminal-only system, verify the exact path with `pwd`, move to its parent, and remove only that verified folder.

Windows:

```powershell
cd "$HOME\Documents\MultiCameraPrinterDashboard"
.\.venv\Scripts\python.exe manage.py remove-autostart
.\.venv\Scripts\python.exe manage.py stop
```

Close PowerShell and browser tabs, then delete the selected `MultiCameraPrinterDashboard` folder in File Explorer so it normally goes to the Recycle Bin.

## Verification and limitations

Run locally:

```bash
python -m pip install -r requirements-dev.txt "opencv-python-headless>=4.8,<5"
python -m pytest
python -m compileall -q app setup.py manage.py
node --check app/static/app.js
```

The automated suite verifies exact port rules/fallback, saved settings, legacy-login migration, multi-user roles/limits/password changes, authentication/CSRF, credential redaction, camera CRUD and six-camera enforcement, simultaneous capture workers, reconnection, video transforms, browser capture controls, Moonraker native-layer/Z-height normalization and port-7125 fallback, private printer settings and URL validation, autostart definitions/status/repair, startup information, and data-preserving upgrades. GitHub Actions runs Python tests on Windows and Ubuntu.

Development verification cannot simulate every physical Raspberry Pi boot, USB driver, RTSP vendor, codec, Tailscale policy/subnet route, browser download rule, multi-hour timelapse, or printer firmware UI. Those hardware- and network-specific cases must be tested on the target devices. During version 1.2.0 diagnosis, the configured live Moonraker endpoint was reachable and reported an active print with a null native layer plus a valid Z height; the fallback parsing is also covered with deterministic tests. The upgraded build still requires installation on the target Pi before claiming a full multi-layer hardware run.

## License

MIT; see [LICENSE](LICENSE).
