from unittest.mock import Mock

import pytest

from ui import launch


@pytest.fixture(autouse=True)
def _no_live_feed(monkeypatch):
    """The launcher starts the Bloomberg feed; keep tests off the network / status file."""
    monkeypatch.setenv("RISK_LIVE", "0")


def _me():
    return launch.identity(launch.source_fingerprint())


def _probe_by_port(mapping):
    """mapping: port -> identity string (None = nothing listening)."""
    def fake(url):
        return mapping.get(int(url.rsplit(':', 1)[1]))
    return fake


def _run_server_capture(monkeypatch):
    """Patch make_server / Thread so main() starts a fake server and exits on first join."""
    made = []

    def make_server(host, port, wsgi, threaded=True):
        made.append(port)
        return Mock()
    monkeypatch.setattr('werkzeug.serving.make_server', make_server)
    worker = Mock()
    worker.is_alive.return_value = True
    worker.join.side_effect = KeyboardInterrupt
    monkeypatch.setattr(launch.threading, 'Thread', lambda **k: worker)
    return made


def test_fingerprint_is_stable_and_changes_with_source(tmp_path):
    (tmp_path / 'ui').mkdir()
    (tmp_path / 'ui' / 'a.py').write_text('x = 1')
    first = launch.source_fingerprint(tmp_path)
    assert first == launch.source_fingerprint(tmp_path) and len(first) == 16
    (tmp_path / 'ui' / 'a.py').write_text('x = 2')
    assert launch.source_fingerprint(tmp_path) != first


def test_matching_identity_reuses_running_instance(monkeypatch):
    monkeypatch.setattr(launch, 'probe', _probe_by_port({8050: _me()}))
    browser = Mock()
    monkeypatch.setattr(launch.webbrowser, 'open', browser)
    made = _run_server_capture(monkeypatch)
    assert launch.main([]) == 0
    browser.assert_called_once_with('http://127.0.0.1:8050')
    assert made == []


def test_stale_identity_is_not_reused_and_next_port_used(monkeypatch, capsys):
    monkeypatch.setattr(launch, 'probe', _probe_by_port({8050: launch.identity('0000deadbeef0000')}))
    opened = []
    monkeypatch.setattr(launch.webbrowser, 'open', opened.append)
    made = _run_server_capture(monkeypatch)
    assert launch.main([]) == 0
    assert made == [8051] and opened == ['http://127.0.0.1:8051']
    out = capsys.readouterr().out
    assert 'STALE code, not reused' in out and 'left running' in out
    for label in ('Interpreter:', 'Working dir:', 'Database:', 'App version:', 'Risk monitor: http://127.0.0.1:8051'):
        assert label in out


def test_force_new_skips_matching_instance(monkeypatch, capsys):
    monkeypatch.setattr(launch, 'probe', _probe_by_port({8050: _me()}))
    opened = []
    monkeypatch.setattr(launch.webbrowser, 'open', opened.append)
    made = _run_server_capture(monkeypatch)
    assert launch.main(['--force-new']) == 0
    assert made == [8051] and opened == ['http://127.0.0.1:8051']
    assert 'forced new instance' in capsys.readouterr().out


def test_occupied_ports_skipped_and_all_occupied_fails(monkeypatch, capsys):
    monkeypatch.setattr(launch, 'probe', _probe_by_port({8050: 'some other app'}))
    monkeypatch.setattr(launch.webbrowser, 'open', lambda url: None)
    calls = []

    def make_server(host, port, wsgi, threaded=True):
        calls.append(port)
        if port == 8051:
            raise OSError('in use')
        return Mock()
    monkeypatch.setattr('werkzeug.serving.make_server', make_server)
    worker = Mock()
    worker.is_alive.return_value = True
    worker.join.side_effect = KeyboardInterrupt
    monkeypatch.setattr(launch.threading, 'Thread', lambda **k: worker)
    assert launch.main([]) == 0
    assert calls == [8051, 8052]
    assert 'occupied by another application' in capsys.readouterr().out

    monkeypatch.setattr('werkzeug.serving.make_server', Mock(side_effect=OSError('in use')))
    monkeypatch.setattr(launch, 'probe', lambda url: None)
    assert launch.main([]) == 1
    assert 'Cannot start' in capsys.readouterr().out


def test_launcher_starts_before_opening_browser_and_closes(monkeypatch):
    monkeypatch.setattr(launch, 'probe', lambda url: None)
    server = Mock()
    monkeypatch.setattr('werkzeug.serving.make_server', lambda *a, **k: server)
    worker = Mock()
    worker.is_alive.return_value = True
    worker.join.side_effect = KeyboardInterrupt
    monkeypatch.setattr(launch.threading, 'Thread', lambda **k: worker)

    def browser(url):
        worker.start.assert_called_once()
        assert url == 'http://127.0.0.1:8050'
    monkeypatch.setattr(launch.webbrowser, 'open', browser)
    assert launch.main([]) == 0
    server.shutdown.assert_called_once()
    server.server_close.assert_called_once()
