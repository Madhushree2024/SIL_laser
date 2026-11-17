from io import BytesIO
import socket

import pytest

import launch


@pytest.mark.parametrize("body,expected", [
    (b'{"application":"areca-cutter","status":"ready"}', True),
    (b'{"application":"other-app"}', False),
    (b'not json', False),
])
def test_readiness_identifies_application(monkeypatch, body, expected):
    monkeypatch.setattr(launch, "urlopen", lambda url, timeout: BytesIO(body))
    assert launch.is_ready("http://127.0.0.1:8000") is expected


def test_launcher_skips_occupied_port():
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        preferred = occupied.getsockname()[1]
        if preferred > 65515:
            pytest.skip("Ephemeral port too near end of range")
        chosen = launch.select_port(preferred)
        assert chosen != preferred
        with socket.socket() as available:
            available.bind(("127.0.0.1", chosen))
