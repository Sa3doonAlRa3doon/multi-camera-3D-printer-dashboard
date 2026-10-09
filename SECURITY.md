# Security

## Private data

The installer stores authentication and printer-dashboard settings in `config/settings.json` and camera definitions in `data/cameras.json` inside the selected installation folder. Network-camera credentials may be present in `data/cameras.json`, and the printer's private network address may be present in `config/settings.json`. Both folders are excluded by `.gitignore`; never commit, attach, or publish them. The installer restricts their filesystem permissions to the installing user where the platform permits it.

The API never returns password hashes, camera usernames/passwords, embedded URL credentials, or private URL query values. Administrators can list account usernames, roles, and enabled state so they can manage the installation. Editing a network camera with blank credential fields preserves its existing private values.

Printer dashboard and Moonraker API URLs must use HTTP or HTTPS and cannot contain embedded username/password values. The dashboard URL is returned only to an authenticated session because the viewing browser connects to the printer directly. The server queries only `/printer/objects/query?print_stats` on the configured/automatically derived Moonraker origin for layer timelapse. Configure only printer addresses you trust; this makes an outbound request from the application host. Prefer the printer UI's own authentication if it offers one.

## Network use

All viewer and management endpoints require a signed-in, enabled account. State-changing requests also require a per-session CSRF token. Administrator authorization protects camera, printer, startup, network, and account management. Viewer accounts can view streams and make browser-local captures but cannot change server configuration. At least one enabled administrator is enforced. The health endpoint contains only the application name, version, and status.

The built-in server uses HTTP. Authentication prevents casual unauthorized access, but HTTP itself does not encrypt traffic. Use it only on a trusted LAN or over an already-connected Tailscale network. For untrusted networks, place the viewer behind a correctly configured HTTPS reverse proxy. Do not expose its port directly to the public internet.

The application does not proxy the full dashboard, rewrite it, or weaken its security headers. It returns only normalized print state, filename, layer numbers, and Z height from Moonraker's print-status response. If the dashboard blocks iframe embedding, use the full-page link. Reaching a LAN-only printer page from a remote Tailscale client requires an authorized Tailscale address or subnet route configured outside this application.

## Reporting a vulnerability

Open a GitHub security advisory for this repository rather than placing credentials, private stream URLs, or exploit details in a public issue.
