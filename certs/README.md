# SSL/TLS Certificates

This directory stores SSL/TLS certificates for enabling HTTPS on the admin console.

## Quick Start (Self-Signed Certificate)

Generate a self-signed certificate for development/personal use:

```bash
openssl req -x509 -newkey rsa:4096 -nodes \
  -out cert.pem -keyout key.pem -days 365 \
  -subj "/CN=localhost"
```

Then configure in your `.env` file:

```bash
export CINDY_ADMIN_CONSOLE_SSL_CERT="$(pwd)/certs/cert.pem"
export CINDY_ADMIN_CONSOLE_SSL_KEY="$(pwd)/certs/key.pem"
```

## Files

- `cert.pem` - SSL certificate (public key)
- `key.pem` - SSL private key (keep secret!)

## Security Notes

⚠️ **Important:**
- Self-signed certificates will trigger browser security warnings
- For production use, obtain certificates from Let's Encrypt or another Certificate Authority
- Keep your private key (`key.pem`) secure and never commit it to version control
- Certificates expire after the specified days (365 by default) and need renewal

## Production Deployment

For production deployments, consider:

1. **Let's Encrypt** (free, automated certificates)
   - Use Certbot or similar tools
   - Automatic renewal

2. **Reverse Proxy** (recommended)
   - Nginx or Apache handles SSL termination
   - Better performance and more features
   - See `tmp/https-options.md` for details

3. **Cloud Providers**
   - AWS Certificate Manager
   - Google Cloud SSL certificates
   - Azure Key Vault certificates

## Certificate Renewal

Self-signed certificates need manual renewal before expiration:

```bash
# Check expiration date
openssl x509 -in cert.pem -noout -dates

# Generate new certificate (same command as initial generation)
openssl req -x509 -newkey rsa:4096 -nodes \
  -out cert.pem -keyout key.pem -days 365 \
  -subj "/CN=localhost"

# Restart the server
./run.sh restart
```

## Troubleshooting

**Browser shows "Your connection is not private" warning:**
- Expected with self-signed certificates
- Click "Advanced" → "Proceed to localhost (unsafe)" for development
- For production, use certificates from a trusted Certificate Authority

**Certificate loading errors:**
- Verify both cert.pem and key.pem exist and are readable
- Check file permissions (should be readable by the user running the server)
- Ensure paths in environment variables are correct

**Session cookies not working:**
- When using HTTPS, ensure `CINDY_ADMIN_CONSOLE_SECRET_KEY` is set
- Clear browser cookies and try again

## More Information

See the comprehensive HTTPS deployment guide:
- `tmp/https-options.md` - Detailed comparison of HTTPS deployment options
