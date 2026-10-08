# Security

## Private data

The installer stores authentication and printer-dashboard settings in `config/settings.json` and camera definitions in `data/cameras.json` inside the selected installation folder. Network-camera credentials may be present in `data/cameras.json`, and the printer's private network address may be present in `config/settings.json`. Both folders are excluded by `.gitignore`; never commit, attach, or publish them. The installer restricts their filesystem permissions to the installing user where the platform permits it.

The API never returns saved usernames, passwords, embedded URL credentials, or URL query values. Editing a network camera with blank credential fields preserves its existing private values.

Printer dashboard URLs must use HTTP or HTTPS and cannot contain embedded username/password values. The URL is returned only to an authenticated session because the viewing browser connects to the printer directly. The server also queries only `/printer/objects/query?print_stats` on that configured origin for layer timelapse. Configure only a printer address you trust; this makes an outbound request from the application host. Prefer the printer UI's own authentication if it offers one.

## Network use

All viewer and management endpoints require a signed-in session. State-changing requests also require a per-session CSRF token. The health endpoint contains only the application name, version, and status.

The built-in server uses HTTP. Authentication prevents casual unauthorized access, but HTTP itself does not encrypt traffic. Use it only on a trusted LAN or over an already-connected Tailscale network. For untrusted networks, place the viewer behind a correctly configured HTTPS reverse proxy. Do not expose its port directly to the public internet.

The application does not proxy the full dashboard, rewrite it, or weaken its security headers. It returns only normalized print state, filename, and layer numbers from Moonraker's print-status response. If the dashboard blocks iframe embedding, use the full-page link. Reaching a LAN-only printer page from a remote Tailscale client requires an authorized Tailscale address or subnet route configured outside this application.

## Reporting a vulnerability

Open a GitHub security advisory for this repository rather than placing credentials, private stream URLs, or exploit details in a public issue.
