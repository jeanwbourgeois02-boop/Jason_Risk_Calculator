"""Launch the local app and open its browser once the server is ready.

Duplicate-server protection with a source fingerprint: a running risk-monitor instance
is reused only when it reports the SAME fingerprint of the current source tree. A stale
instance (different fingerprint) is left running -- never terminated -- and the new app
starts on the next free port. `--force-new` skips reuse detection entirely.
"""
import argparse
import hashlib
import os
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.request import urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ("ui", "engine", "data/ingest", "data/bloomberg")
IDENTITY_ROUTE = '/_risk_monitor_identity'
IDENTITY_PREFIX = 'risk-monitor:'
SHUTDOWN_ROUTE = '/_risk_monitor_shutdown'
PORTS = range(8050, 8061)


def source_fingerprint(root: Path = REPO_ROOT) -> str:
    """Short sha256 over every .py file under SOURCE_DIRS (sorted paths + contents)."""
    digest = hashlib.sha256()
    for sub in SOURCE_DIRS:
        for path in sorted((root / sub).rglob('*.py')):
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def identity(fingerprint: str) -> str:
    return f'{IDENTITY_PREFIX}{fingerprint}'


def probe(url: str):
    """Identity string reported by a server at url, or None if nothing answers."""
    try:
        with urlopen(url + IDENTITY_ROUTE, timeout=0.3) as response:
            return response.read().decode()
    except Exception:
        return None


def stop_instance(url: str, wait_s: float = 5.0) -> bool:
    """Ask a risk-monitor instance at url to exit, then wait until the port is free.
    Instances started from code older than this route ignore the request; the caller
    then reports them and moves to the next port."""
    import time
    from urllib.request import Request
    try:
        with urlopen(Request(url + SHUTDOWN_ROUTE, method='POST'), timeout=1.0):
            pass
    except Exception:
        return False
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if probe(url) is None:
            return True
        time.sleep(0.2)
    return False


def _shutdown_view():
    """Exit this process shortly after answering, so a newer launcher can take the port."""
    threading.Timer(0.3, os._exit, (0,)).start()
    return 'stopping'


def parse_args(argv):
    parser = argparse.ArgumentParser(description='Launch the risk monitor.')
    parser.add_argument('--force-new', action='store_true',
                        help='never reuse a running instance; start on the next free port')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args([] if argv is None else argv)
    try:
        from ui.app import create_app, get_db_path
        import openpyxl
        import xlrd
    except ImportError as exc:
        print(f'Missing dependency: {exc.name}. Run: py -3 -m pip install -r requirements.txt')
        return 1
    from werkzeug.serving import make_server
    fingerprint = source_fingerprint()
    me = identity(fingerprint)
    app = create_app(start_feed=True)  # Bloomberg live feed when this computer has it
    app.server.add_url_rule(IDENTITY_ROUTE, view_func=lambda: me)
    app.server.add_url_rule(SHUTDOWN_ROUTE, view_func=_shutdown_view, methods=['POST'])
    server = url = None
    for port in PORTS:
        url = f'http://127.0.0.1:{port}'
        seen = probe(url)
        if seen is not None:
            if seen == me and not args.force_new:
                webbrowser.open(url)
                print(f'Risk monitor is already running with the current code: {url}')
                return 0
            if seen.startswith(IDENTITY_PREFIX):
                why = 'forced new instance' if args.force_new else 'STALE code'
                if stop_instance(url):
                    print(f'Port {port}: stopped risk-monitor instance {seen} ({why}).')
                else:
                    print(f'Port {port}: risk-monitor instance {seen} ({why}) did not stop; '
                          f'close its terminal. Trying the next port.')
                    continue
            else:
                print(f'Port {port}: occupied by another application.')
                continue
        try:
            server = make_server('127.0.0.1', port, app.server, threaded=True)
            break
        except (OSError, SystemExit):
            print(f'Port {port}: occupied.')
            continue
    if server is None:
        print('Cannot start: ports 8050-8060 are occupied.')
        return 1
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    print(f'Interpreter:  {sys.executable}\n'
          f'Working dir:  {os.getcwd()}\n'
          f'Database:     {get_db_path()}\n'
          f'App version:  {fingerprint}\n'
          f'Risk monitor: {url}\nKeep this terminal open. Press Ctrl+C to stop.', flush=True)
    webbrowser.open(url)
    try:
        while worker.is_alive():
            worker.join(0.5)
    except KeyboardInterrupt:
        server.shutdown()
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
