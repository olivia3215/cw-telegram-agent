# HTTPS Quick Start Guide

This guide shows how to enable HTTPS for the cw-telegram-agent Admin Console.

## For Personal/Development Use (Self-Signed Certificate)

### 1. Generate SSL Certificate

The certificate is already generated in `./certs/`. If you need to regenerate:

```bash
openssl req -x509 -newkey rsa:4096 -nodes \
  -out certs/cert.pem -keyout certs/key.pem -days 365 \
  -subj "/CN=localhost"
```

### 2. Enable HTTPS

**Option A: Add to .env file (permanent)**

Uncomment these lines in your `.env` file:

```bash
export CINDY_ADMIN_CONSOLE_SSL_CERT="$SCRIPT_DIR/certs/cert.pem"
export CINDY_ADMIN_CONSOLE_SSL_KEY="$SCRIPT_DIR/certs/key.pem"
```

**Option B: Set environment variables (temporary)**

```bash
export CINDY_ADMIN_CONSOLE_SSL_CERT="$(pwd)/certs/cert.pem"
export CINDY_ADMIN_CONSOLE_SSL_KEY="$(pwd)/certs/key.pem"
```

### 3. Start/Restart the Server

```bash
./run.sh restart
```

### 4. Access via HTTPS

Open your browser to: **https://localhost:5001/admin**

**Browser Warning:** Self-signed certificates show a security warning. Click "Advanced" → "Proceed to localhost (unsafe)".

## Disabling HTTPS

To switch back to HTTP:

1. Comment out or remove the SSL environment variables in `.env`
2. Restart the server: `./run.sh restart`
3. Access via: http://localhost:5001/admin

## Checking Certificate Expiration

```bash
openssl x509 -in certs/cert.pem -noout -dates
```

Self-signed certificates expire after 365 days and need renewal.

## For Production Deployments

For production use with proper SSL certificates:

- **Option 1:** Use Let's Encrypt with Nginx reverse proxy (recommended)
- **Option 2:** Use a cloud provider's SSL certificate service
- **Option 3:** Use Cloudflare Tunnel for automatic HTTPS

See `tmp/https-options.md` for detailed deployment options and comparisons.

## Troubleshooting

**"Failed to load SSL certificates" error:**
- Verify both cert.pem and key.pem exist in the `certs/` directory
- Check file permissions (should be readable)
- Ensure paths in environment variables are correct

**Browser won't connect:**
- Make sure you're using `https://` not `http://`
- Try clearing browser cache and cookies
- Verify the server started with HTTPS (check logs)

**Session cookies not working:**
- Ensure `CINDY_ADMIN_CONSOLE_SECRET_KEY` is set in `.env`
- Clear browser cookies and try again

## Security Notes

⚠️ **Important:**
- Self-signed certificates are fine for personal/development use
- For production, use certificates from a trusted Certificate Authority
- Never commit private keys (`key.pem`) to version control
- The `.gitignore` file already excludes the `certs/` directory

## More Information

- **Full HTTPS options guide:** `tmp/https-options.md`
- **Certificate management:** `certs/README.md`
- **Admin console docs:** `ADMIN_CONSOLE.md`
