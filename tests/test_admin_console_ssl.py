# tests/test_admin_console_ssl.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Tests for admin console SSL/HTTPS functionality.
"""

import ssl
import tempfile
from pathlib import Path

import pytest

from admin_console.app import start_admin_console


def test_admin_console_starts_with_http_when_no_ssl():
    """Test that admin console starts with HTTP when SSL certs are not provided."""
    # Start server without SSL
    server = start_admin_console("127.0.0.1", 0)  # Port 0 = random available port
    
    try:
        # Server should be running
        assert server is not None
        # SSL context should not be set (HTTP mode)
        assert server.ssl_context is None
    finally:
        server.shutdown()


def test_admin_console_warns_when_only_cert_provided(caplog):
    """Test that admin console warns when only certificate is provided without key."""
    with tempfile.NamedTemporaryFile(suffix=".pem") as cert:
        # Start server with only cert (no key)
        server = start_admin_console("127.0.0.1", 0, ssl_cert=cert.name, ssl_key=None)
        
        try:
            # Server should fall back to HTTP
            assert server.ssl_context is None
            # Should log a warning
            assert "Both CINDY_ADMIN_CONSOLE_SSL_CERT and CINDY_ADMIN_CONSOLE_SSL_KEY must be set" in caplog.text
        finally:
            server.shutdown()


def test_admin_console_warns_when_only_key_provided(caplog):
    """Test that admin console warns when only key is provided without certificate."""
    with tempfile.NamedTemporaryFile(suffix=".pem") as key:
        # Start server with only key (no cert)
        server = start_admin_console("127.0.0.1", 0, ssl_cert=None, ssl_key=key.name)
        
        try:
            # Server should fall back to HTTP
            assert server.ssl_context is None
            # Should log a warning
            assert "Both CINDY_ADMIN_CONSOLE_SSL_CERT and CINDY_ADMIN_CONSOLE_SSL_KEY must be set" in caplog.text
        finally:
            server.shutdown()


def test_admin_console_falls_back_to_http_on_invalid_certs(caplog):
    """Test that admin console falls back to HTTP when SSL certificates are invalid."""
    with tempfile.NamedTemporaryFile(suffix=".pem") as cert, \
         tempfile.NamedTemporaryFile(suffix=".pem") as key:
        # Write invalid data to cert and key files
        cert.write(b"not a valid certificate")
        cert.flush()
        key.write(b"not a valid key")
        key.flush()
        
        # Start server with invalid certs
        server = start_admin_console("127.0.0.1", 0, ssl_cert=cert.name, ssl_key=key.name)
        
        try:
            # Server should fall back to HTTP
            assert server.ssl_context is None
            # Should log an error about loading certificates
            assert "Failed to load SSL certificates" in caplog.text
            assert "Falling back to HTTP" in caplog.text
        finally:
            server.shutdown()
