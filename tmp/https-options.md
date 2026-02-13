# HTTPS Options for cw-telegram-agent

**Issue:** [#547](https://github.com/olivia3215/cw-telegram-agent/issues/547) - What are our options for switching to https?

**Date:** February 12, 2026

---

## Current State

The application currently runs the Admin Console as an HTTP-only Flask web server:
- Default host: `0.0.0.0` (all interfaces)
- Default port: `5001`
- Using Werkzeug's built-in development server
- Configuration via environment variables: `CINDY_ADMIN_CONSOLE_HOST` and `CINDY_ADMIN_CONSOLE_PORT`

The main entry point is in `src/admin_console/app.py` which uses `werkzeug.serving.make_server()`.

---

## Why HTTPS?

**Security Benefits:**
1. **Encryption**: Protects sensitive data in transit (API keys, session cookies, conversation data)
2. **Authentication**: Verifies server identity and prevents man-in-the-middle attacks
3. **Integrity**: Ensures data hasn't been tampered with during transmission
4. **Browser Trust**: Modern browsers show warnings for non-HTTPS sites and may restrict features
5. **Best Practice**: Essential for any production deployment, especially over networks

**Current Security Considerations:**
- The Admin Console has OTP-based authentication
- Session cookies are used for maintaining login state
- API keys and sensitive configuration data are displayed in the UI
- Conversation history and personal data are accessible through the console

---

## Options for HTTPS Implementation

### Option 1: Reverse Proxy (Recommended)

**Description:** Run a reverse proxy (Nginx, Apache, Caddy) in front of the application that handles SSL/TLS termination.

**Architecture:**
```
Internet → HTTPS (443) → Reverse Proxy → HTTP (5001) → Flask App
```

**Pros:**
- ✅ **Industry standard** approach for production deployments
- ✅ **Separation of concerns** - proxy handles SSL, app focuses on business logic
- ✅ **Performance** - dedicated reverse proxies are optimized for SSL/TLS
- ✅ **Flexibility** - can add rate limiting, caching, load balancing, etc.
- ✅ **Easy certificate management** - tools like Certbot for Let's Encrypt
- ✅ **No code changes required** - app continues to run HTTP internally
- ✅ **Mature ecosystem** - well-documented, battle-tested solutions

**Cons:**
- ❌ Requires additional software installation and configuration
- ❌ Slightly more complex setup for local development

**Best For:** Production deployments, remote access, public-facing servers

**Implementation Complexity:** Medium (mostly infrastructure/DevOps work)

---

### Option 2: Built-in Flask/Werkzeug SSL

**Description:** Configure Flask's built-in development server to use SSL certificates directly.

**Architecture:**
```
Internet → HTTPS (5001) → Flask App (with SSL context)
```

**Pros:**
- ✅ **Simple** - minimal configuration, no external dependencies
- ✅ **Quick setup** - good for development and testing
- ✅ **Self-contained** - everything runs within the Python application

**Cons:**
- ❌ **Not recommended for production** - Werkzeug warns against using its dev server in production
- ❌ **Performance** - slower SSL handling compared to dedicated reverse proxies
- ❌ **Limited features** - no advanced SSL configurations (HSTS, OCSP stapling, etc.)
- ❌ **Manual certificate management** - need to handle renewals yourself
- ❌ **Code changes required** - need to modify `src/admin_console/app.py`

**Best For:** Development, testing, small-scale private deployments

**Implementation Complexity:** Low (just code changes)

---

### Option 3: Production WSGI Server with SSL (uWSGI/Gunicorn + SSL)

**Description:** Deploy the Flask app with a production WSGI server that supports SSL.

**Architecture:**
```
Internet → HTTPS (443) → Gunicorn/uWSGI → Flask App
```

**Pros:**
- ✅ **Production-ready** - designed for production use
- ✅ **Better performance** than Werkzeug
- ✅ **SSL support** built-in (especially uWSGI)
- ✅ **Process management** - multiple workers, auto-restart, etc.

**Cons:**
- ❌ **Less common** - most deployments use reverse proxy instead
- ❌ **More complex** than reverse proxy for SSL management
- ❌ **Still benefits from a reverse proxy** for many use cases
- ❌ **Requires code changes** and dependency additions

**Best For:** Production deployments where you want more control than Werkzeug but don't want a separate reverse proxy layer

**Implementation Complexity:** Medium-High

---

### Option 4: Cloudflare Tunnel / ngrok (Tunneling Services)

**Description:** Use a tunneling service that provides HTTPS automatically by creating a secure tunnel to your local HTTP server.

**Architecture:**
```
Internet → HTTPS → Cloudflare/ngrok → HTTP (localhost:5001) → Flask App
```

**Pros:**
- ✅ **Zero configuration** for SSL - automatic certificate management
- ✅ **No port forwarding** needed - works behind NAT/firewalls
- ✅ **No code changes** - works with existing HTTP server
- ✅ **Quick setup** - can be running in minutes
- ✅ **Built-in security features** - DDoS protection, access controls (Cloudflare)

**Cons:**
- ❌ **Requires external service** - dependency on third-party
- ❌ **Latency** - traffic routes through external servers
- ❌ **Cost** - some features require paid plans
- ❌ **Trust concerns** - traffic passes through third-party infrastructure

**Best For:** Quick remote access, development/testing across networks, avoiding firewall configuration

**Implementation Complexity:** Very Low

---

### Option 5: Container Orchestration (Docker + Traefik/Nginx)

**Description:** Containerize the application and use container orchestration with automatic SSL.

**Architecture:**
```
Internet → HTTPS (443) → Traefik/Nginx Container → App Container
```

**Pros:**
- ✅ **Modern infrastructure** - fits well with cloud deployments
- ✅ **Automatic SSL** - Traefik can auto-provision Let's Encrypt certificates
- ✅ **Scalability** - easy to scale and manage multiple instances
- ✅ **Reproducible** - consistent environment across deployments
- ✅ **Service discovery** - automatic routing to services

**Cons:**
- ❌ **Significant overhead** for small deployments
- ❌ **Learning curve** - requires Docker/orchestration knowledge
- ❌ **Complexity** - overkill for single-server deployments

**Best For:** Cloud deployments, microservices architecture, teams familiar with containers

**Implementation Complexity:** High

---

## Detailed Implementation Guides

### Option 1: Nginx Reverse Proxy (Recommended Implementation)

#### Step 1: Install Nginx

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install nginx

# macOS
brew install nginx
```

#### Step 2: Configure the Admin Console for Local-Only Access

Update your `.env` file:
```bash
export CINDY_ADMIN_CONSOLE_HOST=127.0.0.1  # Only accept local connections
export CINDY_ADMIN_CONSOLE_PORT=5001
```

This ensures the Flask app only accepts connections from localhost (via the reverse proxy).

#### Step 3: Generate SSL Certificates

**For Production (Let's Encrypt):**
```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

**For Development (Self-Signed):**
```bash
# Create directory for certificates
sudo mkdir -p /etc/nginx/ssl

# Generate self-signed certificate (valid for 365 days)
sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout /etc/nginx/ssl/cw-agent.key \
  -out /etc/nginx/ssl/cw-agent.crt \
  -subj "/CN=localhost"
```

#### Step 4: Configure Nginx

Create `/etc/nginx/sites-available/cw-admin-console`:

```nginx
# HTTP server - redirect to HTTPS
server {
    listen 80;
    server_name your-domain.com;  # or localhost for local dev
    return 301 https://$server_name$request_uri;
}

# HTTPS server
server {
    listen 443 ssl http2;
    server_name your-domain.com;  # or localhost for local dev

    # SSL certificate configuration
    ssl_certificate /etc/nginx/ssl/cw-agent.crt;
    ssl_certificate_key /etc/nginx/ssl/cw-agent.key;
    
    # For Let's Encrypt, use:
    # ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    # ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    # SSL security settings
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;

    # Proxy configuration
    location / {
        proxy_pass http://127.0.0.1:5001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket support (if needed in future)
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        
        # Timeouts for long-running operations
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    # Increase max body size for media uploads
    client_max_body_size 50M;

    # Logging
    access_log /var/log/nginx/cw-admin-console-access.log;
    error_log /var/log/nginx/cw-admin-console-error.log;
}
```

#### Step 5: Enable the Configuration

```bash
# Enable the site
sudo ln -s /etc/nginx/sites-available/cw-admin-console /etc/nginx/sites-enabled/

# Test configuration
sudo nginx -t

# Restart Nginx
sudo systemctl restart nginx
```

#### Step 6: Update Firewall (if applicable)

```bash
# Allow HTTPS traffic
sudo ufw allow 443/tcp

# Optionally allow HTTP for redirect
sudo ufw allow 80/tcp

# Verify rules
sudo ufw status
```

#### Step 7: Access the Application

- **HTTPS:** https://your-domain.com/admin or https://localhost/admin
- The HTTP version will automatically redirect to HTTPS

**For self-signed certificates:** Your browser will show a security warning. You can:
- Click "Advanced" → "Proceed anyway" (development only!)
- Add the certificate to your system's trusted certificates

---

### Option 2: Flask Built-in SSL (Development/Testing)

#### Step 1: Generate Self-Signed Certificate

```bash
# Create directory in the project
mkdir -p certs
cd certs

# Generate certificate and key
openssl req -x509 -newkey rsa:4096 -nodes \
  -out cert.pem -keyout key.pem -days 365 \
  -subj "/CN=localhost"

cd ..
```

#### Step 2: Modify `src/admin_console/app.py`

Add SSL context support to the `start_admin_console` function:

```python
def start_admin_console(host: str, port: int, ssl_cert: str | None = None, ssl_key: str | None = None):
    """
    Start the admin console web server in a background thread.

    Parameters
    ----------
    host : str
        Host interface to bind to
    port : int
        Port to listen on
    ssl_cert : str | None
        Path to SSL certificate file (for HTTPS)
    ssl_key : str | None
        Path to SSL private key file (for HTTPS)

    Returns
    -------
    werkzeug.serving.BaseWSGIServer
        The server instance; call ``shutdown()`` during cleanup.
    """
    app = create_admin_app()
    
    # Create SSL context if certificates are provided
    ssl_context = None
    if ssl_cert and ssl_key:
        import ssl
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(ssl_cert, ssl_key)
        logger.info("Admin console will use HTTPS")
    
    server = make_server(host, port, app, threaded=True, ssl_context=ssl_context)

    thread = threading.Thread(
        target=server.serve_forever,
        name="AdminConsoleServer",
        daemon=True,
    )
    thread.start()

    protocol = "https" if ssl_context else "http"
    logger.info("Admin console listening on %s://%s:%s/admin", protocol, host, port)
    return server
```

#### Step 3: Update `src/run.py`

Modify the code that starts the admin console to pass SSL certificate paths:

```python
# Add new environment variables for SSL configuration
ssl_cert = os.getenv("CINDY_ADMIN_CONSOLE_SSL_CERT")
ssl_key = os.getenv("CINDY_ADMIN_CONSOLE_SSL_KEY")

# Pass SSL configuration when starting
admin_server = start_admin_console(
    admin_host, 
    admin_port,
    ssl_cert=ssl_cert,
    ssl_key=ssl_key
)
```

#### Step 4: Configure Environment Variables

Update your `.env` file:
```bash
export CINDY_ADMIN_CONSOLE_SSL_CERT="$(pwd)/certs/cert.pem"
export CINDY_ADMIN_CONSOLE_SSL_KEY="$(pwd)/certs/key.pem"
```

#### Step 5: Update Documentation

Add to `README.md` and `ADMIN_CONSOLE.md`:

```markdown
## HTTPS Configuration (Optional)

To enable HTTPS for the admin console:

1. Generate SSL certificates (or obtain from Let's Encrypt)
2. Set environment variables:
   ```bash
   export CINDY_ADMIN_CONSOLE_SSL_CERT="/path/to/cert.pem"
   export CINDY_ADMIN_CONSOLE_SSL_KEY="/path/to/key.pem"
   ```
3. Access the console via https://localhost:5001/admin

**Note:** For production deployments, using a reverse proxy (Nginx, Apache) is recommended over built-in Flask SSL.
```

#### Step 6: Test

```bash
# Start the application
./run.sh start

# Access via HTTPS
open https://localhost:5001/admin
```

---

### Option 4: Cloudflare Tunnel (Quick Remote Access)

#### Step 1: Install cloudflared

```bash
# macOS
brew install cloudflare/cloudflare/cloudflared

# Linux (Ubuntu/Debian)
wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared-linux-amd64.deb
```

#### Step 2: Authenticate with Cloudflare

```bash
cloudflared tunnel login
```

This opens a browser to log in and authorize the tunnel.

#### Step 3: Create a Tunnel

```bash
# Create a tunnel
cloudflared tunnel create cw-admin-console

# Note the tunnel ID that's displayed
```

#### Step 4: Configure the Tunnel

Create `~/.cloudflared/config.yml`:

```yaml
tunnel: <YOUR-TUNNEL-ID>
credentials-file: /home/<username>/.cloudflared/<TUNNEL-ID>.json

ingress:
  - hostname: your-subdomain.yourdomain.com
    service: http://localhost:5001
  - service: http_status:404
```

#### Step 5: Create DNS Record

```bash
cloudflared tunnel route dns cw-admin-console your-subdomain.yourdomain.com
```

#### Step 6: Run the Tunnel

```bash
# Run in foreground (for testing)
cloudflared tunnel run cw-admin-console

# Or run as a service (production)
cloudflared service install
sudo systemctl start cloudflared
sudo systemctl enable cloudflared
```

#### Step 7: Access Your Application

Visit `https://your-subdomain.yourdomain.com/admin`

**Benefits:**
- Automatic HTTPS with valid certificate
- No port forwarding or firewall configuration needed
- Built-in DDoS protection
- Access controls via Cloudflare dashboard

---

## Security Considerations

### Session Cookie Security

If implementing HTTPS, update Flask session cookie configuration in `src/admin_console/app.py`:

```python
def create_admin_app() -> Flask:
    """Create and configure the admin console Flask application."""
    app = Flask(...)
    
    # ... existing secret key setup ...
    
    # Configure secure session cookies
    app.config['SESSION_COOKIE_SECURE'] = True  # Only send over HTTPS
    app.config['SESSION_COOKIE_HTTPONLY'] = True  # Prevent JavaScript access
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # CSRF protection
    
    # ... rest of the function ...
```

**Important:** Only set `SESSION_COOKIE_SECURE = True` if you're actually using HTTPS, otherwise sessions won't work.

### Additional Security Headers

When using a reverse proxy, add these security headers (shown in Nginx config above):
- `Strict-Transport-Security` (HSTS) - force HTTPS for future visits
- `X-Frame-Options` - prevent clickjacking
- `X-Content-Type-Options` - prevent MIME sniffing
- `X-XSS-Protection` - enable browser XSS filters

### Certificate Renewal

**Let's Encrypt (auto-renewal):**
```bash
# Test renewal
sudo certbot renew --dry-run

# Renewal is automatic via cron/systemd timer
```

**Self-signed certificates** must be regenerated before expiration and updated in configuration.

---

## Recommendations

### For Different Use Cases:

1. **Local Development (single developer):**
   - Use HTTP without SSL (current state)
   - Or use self-signed certificate with Option 2 if testing SSL-specific features

2. **Remote Development / Testing:**
   - Use **Cloudflare Tunnel** (Option 4) for quick secure access
   - Or use **Nginx with self-signed cert** (Option 1) if you control the server

3. **Production (private/internal deployment):**
   - Use **Nginx reverse proxy** (Option 1) with Let's Encrypt or internal CA certificates
   - This is the industry-standard approach

4. **Production (public-facing):**
   - Use **Nginx reverse proxy** (Option 1) with Let's Encrypt certificates
   - Consider adding **Cloudflare** as a CDN/DDoS protection layer in front

5. **Cloud Deployment (AWS/GCP/Azure):**
   - Use cloud provider's load balancer with SSL termination
   - Or use **Nginx/Traefik** in containers (Option 5)

### Overall Recommendation:

**For most users: Option 1 (Nginx Reverse Proxy)**

**Reasoning:**
- ✅ Production-ready and widely adopted
- ✅ No code changes required
- ✅ Easy certificate management with Let's Encrypt
- ✅ Additional features (rate limiting, caching, etc.) available
- ✅ Separates concerns - app handles logic, proxy handles SSL
- ✅ Well-documented with extensive community support

**Quick temporary access: Option 4 (Cloudflare Tunnel)**

**Reasoning:**
- ✅ Zero configuration
- ✅ Works behind firewalls
- ✅ Automatic HTTPS with valid certificates
- ✅ Can set up in minutes

---

## Implementation Effort Estimation

| Option | Code Changes | Config Effort | Maintenance | Recommended For |
|--------|-------------|---------------|-------------|-----------------|
| **Option 1: Nginx** | None | Medium | Low | **Production** |
| **Option 2: Flask SSL** | Medium | Low | Medium | Development/Testing |
| **Option 3: WSGI+SSL** | High | Medium | Medium | Advanced deployments |
| **Option 4: Tunnel** | None | Very Low | Low | **Quick remote access** |
| **Option 5: Containers** | High | High | Medium | Cloud/Enterprise |

---

## Next Steps

**To proceed with implementation:**

1. **Determine your use case** (development, testing, production, etc.)
2. **Choose the appropriate option** based on recommendations above
3. **Follow the implementation guide** for your chosen option
4. **Test thoroughly** in a development environment before production
5. **Update documentation** to reflect the HTTPS setup
6. **Configure security settings** (session cookies, headers, etc.)

**Questions to answer:**
- Where will the application be accessed from? (localhost, LAN, internet)
- Do you have a domain name? (affects certificate options)
- What's your deployment environment? (bare metal, VM, cloud, container)
- What's your technical comfort level with infrastructure tools?

---

## References

- [Flask Security Best Practices](https://flask.palletsprojects.com/en/2.3.x/security/)
- [Nginx SSL Configuration](https://nginx.org/en/docs/http/configuring_https_servers.html)
- [Let's Encrypt Documentation](https://letsencrypt.org/docs/)
- [Mozilla SSL Configuration Generator](https://ssl-config.mozilla.org/)
- [Cloudflare Tunnel Documentation](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/)
- [OWASP Transport Layer Protection](https://cheatsheetseries.owasp.org/cheatsheets/Transport_Layer_Protection_Cheat_Sheet.html)
