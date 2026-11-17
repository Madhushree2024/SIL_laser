from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError
import ssl
import pytest
from desktop_runtime import get_json, get_local_bytes


def test_loopback_health_does_not_load_external_certificates(monkeypatch):
    def unavailable_context(*args, **kwargs):
        raise FileNotFoundError('Launcher certificate file unavailable in frozen runtime')
    monkeypatch.setattr(ssl, '_create_default_https_context', unavailable_context)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(401 if self.path == '/private' else 200)
            self.end_headers()
            self.wfile.write(b'{"application":"areca-cutter"}')
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f'http://127.0.0.1:{server.server_port}'
        assert get_json(base + '/api/health') == {'application': 'areca-cutter'}
        with pytest.raises(HTTPError) as error:
            get_json(base + '/private')
        assert error.value.code == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.parametrize('url', ['https://127.0.0.1/', 'http://example.com/', 'file:///secret', 'http://user:pass@127.0.0.1/'])
def test_desktop_requests_are_restricted_to_local_plain_http(url):
    with pytest.raises(ValueError, match='127.0.0.1'):
        get_local_bytes(url)
