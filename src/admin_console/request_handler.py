# src/admin_console/request_handler.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Custom WSGI request handler: no drain after response, and no Connection: close (keep-alive).

Werkzeug's default handler (1) sends Connection: close on every response and (2) runs
a drain loop after each response that reads and discards all socket data. That drain
consumes any pipelined next request, so we never handle it (e.g. CSS after HTML).
Separately, when the browser opens a new connection for the CSS request, that second
connection often never reaches our accept() (observed in logs: we see conn1=HTML,
conn2=first JS, with no connection for CSS). So the CSS fails with ERR_TOO_MANY_RETRIES.

We avoid both problems by (1) not draining after the response, so we never consume
the next request, and (2) not sending Connection: close, so the client reuses the
connection (HTTP/1.1 keep-alive). The browser then sends HTML, then CSS, then JS,
etc. on the same connection, so we only need one connection that reliably reaches us.
"""

from __future__ import annotations

import socket

from werkzeug.exceptions import InternalServerError
from werkzeug.serving import WSGIRequestHandler

try:
    import ssl
    _connection_dropped_errors: tuple[type[BaseException], ...] = (
        ConnectionError,
        socket.timeout,
        ssl.SSLEOFError,
    )
except ImportError:
    _connection_dropped_errors = (ConnectionError, socket.timeout)


class NoDrainWSGIRequestHandler(WSGIRequestHandler):
    """
    Same as Werkzeug's WSGIRequestHandler except:
    - We do not drain the socket after sending the response (so we don't consume
      the next request when the client pipelines).
    - We do not send Connection: close, so the client can reuse the connection
      (keep-alive). That way the CSS and other subresources can be requested on
      the same connection as the HTML, avoiding a second connection that may
      never reach our server (observed: the connection for CSS often does not
      reach accept() when the browser opens it separately).
    """

    def send_header(self, keyword, value):
        if keyword.lower() == "connection" and value.strip().lower() == "close":
            return
        super().send_header(keyword, value)

    def run_wsgi(self) -> None:
        if self.headers.get("Expect", "").lower().strip() == "100-continue":
            self.wfile.write(b"HTTP/1.1 100 Continue\r\n\r\n")

        self.environ = environ = self.make_environ()
        status_set: str | None = None
        headers_set: list[tuple[str, str]] | None = None
        status_sent: str | None = None
        headers_sent: list[tuple[str, str]] | None = None
        chunk_response: bool = False

        def write(data: bytes) -> None:
            nonlocal status_sent, headers_sent, chunk_response
            assert status_set is not None, "write() before start_response"
            assert headers_set is not None, "write() before start_response"
            if status_sent is None:
                status_sent = status_set
                headers_sent = headers_set
                try:
                    code_str, msg = status_sent.split(None, 1)
                except ValueError:
                    code_str, msg = status_sent, ""
                code = int(code_str)
                self.send_response(code, msg)
                header_keys = set()
                for key, value in headers_sent:
                    self.send_header(key, value)
                    header_keys.add(key.lower())

                if (
                    not (
                        "content-length" in header_keys
                        or environ["REQUEST_METHOD"] == "HEAD"
                        or (100 <= code < 200)
                        or code in {204, 304}
                    )
                    and self.protocol_version >= "HTTP/1.1"
                ):
                    chunk_response = True
                    self.send_header("Transfer-Encoding", "chunked")

                self.send_header("Connection", "close")
                self.end_headers()

            assert isinstance(data, bytes), "applications must write bytes"

            if data:
                if chunk_response:
                    self.wfile.write(hex(len(data))[2:].encode())
                    self.wfile.write(b"\r\n")

                self.wfile.write(data)

                if chunk_response:
                    self.wfile.write(b"\r\n")

            self.wfile.flush()

        def start_response(status: str, headers: list[tuple[str, str]], exc_info=None):  # type: ignore
            nonlocal status_set, headers_set
            if exc_info:
                try:
                    if headers_sent:
                        raise exc_info[1].with_traceback(exc_info[2])
                finally:
                    exc_info = None
            elif headers_set:
                raise AssertionError("Headers already set")
            status_set = status
            headers_set = headers
            return write

        def execute(app):  # type: ignore
            application_iter = app(environ, start_response)
            try:
                for data in application_iter:
                    write(data)
                if not headers_sent:
                    write(b"")
                if chunk_response:
                    self.wfile.write(b"0\r\n\r\n")
            finally:
                # Do not drain the socket. The default handler reads and discards
                # all remaining data, which consumes any pipelined next request
                # (e.g. GET /admin/css/... after GET /admin/). We close without
                # reading so the client can retry on a new connection.
                if hasattr(application_iter, "close"):
                    application_iter.close()

        try:
            execute(self.server.app)
        except _connection_dropped_errors as e:
            self.connection_dropped(e, environ)
        except Exception as e:
            if self.server.passthrough_errors:
                raise

            if status_sent is not None and chunk_response:
                self.close_connection = True

            try:
                if status_sent is None:
                    status_set = None
                    headers_set = None
                execute(InternalServerError())
            except Exception:
                pass

            from werkzeug.debug.tbtools import DebugTraceback

            msg = DebugTraceback(e).render_traceback_text()
            self.server.log("error", f"Error on request:\n{msg}")
