#!/usr/bin/env python3
"""Serve the existing 8786 preview with the same public-file allowlist as chat."""
import mimetypes
from http.server import ThreadingHTTPServer

from server import Handler, _static_path


class PreviewHandler(Handler):
    def do_GET(self):
        path = _static_path(self.path)
        if path is None:
            self._send(404, b'{"error":"Not found"}')
            return
        try:
            content = path.read_bytes()
        except OSError:
            self._send(404, b'{"error":"Not found"}')
            return
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if path.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        self._send(200, content, content_type)

    def do_POST(self):
        # All model requests go to the one budget-controlled 8788 gateway.
        self._send(404, b'{"error":"Not found"}')


def main():
    with ThreadingHTTPServer(("127.0.0.1", 8786), PreviewHandler) as server:
        server.daemon_threads = True
        server.serve_forever()


if __name__ == "__main__":
    main()
