# HTTPS Guide

This guide explains practical HTTPS setup for the `cw-telegram-agent` admin console.

## When to Use HTTPS

- Use HTTPS if you access the admin console from another machine or over the internet.
- HTTPS is optional for localhost-only development.

## Quick Start (Self-Signed Certificate)

Use this for personal/development use.

1. Generate a local certificate:

```bash
mkdir -p certs
openssl req -x509 -newkey rsa:4096 -nodes \
  -out certs/cert.pem -keyout certs/key.pem -days 365 \
  -subj "/CN=localhost"
```

2. Configure SSL environment variables:

```bash
# If SCRIPT_DIR is available from your .env setup:
export CINDY_ADMIN_CONSOLE_SSL_CERT="$SCRIPT_DIR/certs/cert.pem"
export CINDY_ADMIN_CONSOLE_SSL_KEY="$SCRIPT_DIR/certs/key.pem"
```

Or use explicit absolute paths:

```bash
export CINDY_ADMIN_CONSOLE_SSL_CERT="$(pwd)/certs/cert.pem"
export CINDY_ADMIN_CONSOLE_SSL_KEY="$(pwd)/certs/key.pem"
```

3. Restart:

```bash
./run.sh restart
```

4. Open the admin console:

```bash
URL="https://localhost:${CINDY_ADMIN_CONSOLE_PORT:-5001}/admin"
# Linux:
xdg-open "$URL"
# macOS:
# open "$URL"
```

Self-signed certificates trigger a browser warning. For development use, continue via the browser's advanced prompt.

To disable HTTPS, unset or remove `CINDY_ADMIN_CONSOLE_SSL_CERT` and `CINDY_ADMIN_CONSOLE_SSL_KEY`, then restart.

## Remote Access Option: Cloudflare Tunnel

Cloudflare Tunnel is useful when you want remote HTTPS access without opening inbound ports.

### Why use it

- Automatic HTTPS certificates
- No router port forwarding required
- Works well behind NAT/firewalls

### Basic flow

1. Keep admin console local-only:

```bash
export CINDY_ADMIN_CONSOLE_HOST=127.0.0.1
export CINDY_ADMIN_CONSOLE_PORT=5001
```

2. Install and authenticate `cloudflared` (see Cloudflare docs).
3. Create a tunnel and map a hostname.
4. Route the tunnel to `http://localhost:5001`.
5. Access your console at `https://<your-hostname>/admin`.

For production, the recommended default remains a reverse proxy (Nginx/Apache/Caddy) with trusted certificates.

## Certificate Check

Check certificate validity dates:

```bash
openssl x509 -in certs/cert.pem -noout -dates
```

Regenerate self-signed certificates before expiry.
