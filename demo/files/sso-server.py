#!/usr/bin/python3
"""HTTPS server for the README demo (runs as root, e.g. on 0.0.0.0:443).

Serves the fictional pages of the demo for two virtual hosts that /etc/hosts
points at 127.0.0.1:

  vpn.contoso.example     GlobalProtect portal   -> <www>/vpn/
  login.contoso.example   sign-in page           -> <www>/login/

Both hosts accept the control endpoints:

  /__demo/complete   (GET or POST) create <state-dir>/auth-done, answer 204;
                     the fake gpauth waits for that file
  /__demo/reset      (GET or POST) remove the flag again, answer 204

The flag is also removed at startup. Nothing is cached (Cache-Control:
no-store) and the request log is off.

Usage: sso-server.py --cert C --key K --www DIR [--state-dir /run/gp-demo]
                     [--port 443] [--bind 0.0.0.0]
"""

import argparse
import mimetypes
import os
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

HOST_DIRS = {
    "vpn.contoso.example": "vpn",
    "login.contoso.example": "login",
}

mimetypes.add_type("image/svg+xml", ".svg")


class Handler(BaseHTTPRequestHandler):
    server_version = "contoso-sso"
    protocol_version = "HTTP/1.1"
    timeout = 30  # idle keep-alive and speculative browser connections

    def setup(self):
        # The TLS handshake runs here, in the request thread: a browser's idle
        # speculative connection must not block the accept loop
        self.request.settimeout(self.timeout)
        self.request.do_handshake()
        super().setup()

    def log_message(self, fmt, *args):
        pass

    def _reply(self, status, body=b"", content_type="text/plain; charset=utf-8"):
        self.send_response(status)
        self.send_header("Cache-Control", "no-store")
        if status != 204:
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if status != 204 and self.command != "HEAD":
            self.wfile.write(body)

    def _drain_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)

    def _flag(self):
        return os.path.join(self.server.state_dir, "auth-done")

    def _control(self, path):
        if path == "/__demo/complete":
            os.makedirs(self.server.state_dir, mode=0o755, exist_ok=True)
            os.chmod(self.server.state_dir, 0o755)
            with open(self._flag(), "w", encoding="utf-8"):
                pass
            os.chmod(self._flag(), 0o644)
        else:
            remove_flag(self._flag())
        self._reply(204)

    def _handle(self):
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        path = unquote(urlsplit(self.path).path)
        if path in ("/__demo/complete", "/__demo/reset") and host in HOST_DIRS:
            if self.command == "POST":
                self._drain_body()
            return self._control(path)
        if self.command == "POST":
            self._drain_body()
            return self._reply(405, b"Method not allowed\n")
        site = HOST_DIRS.get(host)
        if site is None:
            return self._reply(404, b"Unknown host\n")

        root = os.path.realpath(os.path.join(self.server.www, site))
        # /global-protect/prelogin.esp is the portal page, whatever the query
        if path == "/global-protect/prelogin.esp":
            path = "/global-protect/prelogin.html"
        elif path.endswith("/"):
            path += "index.html"
        target = os.path.realpath(os.path.join(root, path.lstrip("/")))
        inside = os.path.commonpath([root, target]) == root
        if site == "login" and "." not in os.path.basename(path):
            # any /common/oauth2/authorize?... URL is the sign-in page
            target = os.path.join(root, "index.html")
        elif not inside or not os.path.isfile(target):
            return self._reply(404, b"Not found\n")
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        with open(target, "rb") as handle:
            self._reply(200, handle.read(), ctype)

    do_GET = do_HEAD = do_POST = _handle


def remove_flag(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--www", required=True)
    parser.add_argument("--state-dir", default="/run/gp-demo")
    parser.add_argument("--port", type=int, default=443)
    parser.add_argument("--bind", default="0.0.0.0")
    args = parser.parse_args()

    os.makedirs(args.state_dir, mode=0o755, exist_ok=True)
    os.chmod(args.state_dir, 0o755)
    remove_flag(os.path.join(args.state_dir, "auth-done"))

    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    server.daemon_threads = True
    server.handle_error = lambda request, client_address: None  # aborted TLS
    server.www = os.path.abspath(args.www)
    server.state_dir = args.state_dir
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(args.cert, args.key)
    server.socket = context.wrap_socket(
        server.socket, server_side=True, do_handshake_on_connect=False
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
