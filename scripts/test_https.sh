#!/bin/bash
#
# Test script to verify HTTPS functionality
# This script temporarily enables HTTPS and starts the admin console

set -e

echo "=== HTTPS Test for Admin Console ==="
echo ""

# Check if certificates exist
if [ ! -f "certs/cert.pem" ] || [ ! -f "certs/key.pem" ]; then
    echo "❌ SSL certificates not found. Generating..."
    mkdir -p certs
    openssl req -x509 -newkey rsa:4096 -nodes \
      -out certs/cert.pem -keyout certs/key.pem -days 365 \
      -subj "/CN=localhost"
    echo "✅ SSL certificates generated in ./certs/"
else
    echo "✅ SSL certificates found"
fi

echo ""
echo "Certificate details:"
openssl x509 -in certs/cert.pem -noout -subject -dates
echo ""

# Get absolute path to certs
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Export SSL environment variables
export CINDY_ADMIN_CONSOLE_SSL_CERT="$SCRIPT_DIR/certs/cert.pem"
export CINDY_ADMIN_CONSOLE_SSL_KEY="$SCRIPT_DIR/certs/key.pem"

echo "Environment variables set:"
echo "  CINDY_ADMIN_CONSOLE_SSL_CERT=$CINDY_ADMIN_CONSOLE_SSL_CERT"
echo "  CINDY_ADMIN_CONSOLE_SSL_KEY=$CINDY_ADMIN_CONSOLE_SSL_KEY"
echo ""

echo "To enable HTTPS permanently, add these lines to your .env file:"
echo ""
echo "  export CINDY_ADMIN_CONSOLE_SSL_CERT=\"\$(pwd)/certs/cert.pem\""
echo "  export CINDY_ADMIN_CONSOLE_SSL_KEY=\"\$(pwd)/certs/key.pem\""
echo ""

echo "Note: Self-signed certificates will show a browser warning."
echo "Click 'Advanced' → 'Proceed to localhost (unsafe)' to continue."
echo ""
echo "For production use, see tmp/https-options.md for proper SSL setup."
echo ""
