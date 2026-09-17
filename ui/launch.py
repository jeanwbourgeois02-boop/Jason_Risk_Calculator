"""Launch the local app and open its browser once the server is ready.

Duplicate-server protection with a source fingerprint: a running risk-monitor instance
is reused only when it reports the SAME fingerprint of the current source tree. A stale
instance (different fingerprint) is asked to stop, and force-stopped when it predates the
shutdown route (2026-09-17), so the new app always takes port 8050 and an old bookmark
never shows old code. Another application on a port is left alone and the next port is
used. `--force-new` skips reuse detection entirely.
"""
import argparse
import hashlib
import logging
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


def build_label(root: Path = REPO_ROOT) -> str:
    """Short git commit of the code on disk ('d27cdaa', with '+' when the tree has local
    edits), or the first 7 characters of the source fingerprint outside a git clone.
    Printed at start and shown in the app's top bar so anyone can see what is running."""
    import subprocess
    # stdout pipe only and no timeout: that is subprocess's single-pipe path, which reads
    # inline instead of spawning reader threads (main() runs with threading.Thread
    # replaced by a mock in the launcher tests).
    opts = dict(cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        r = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], **opts)
        if r.returncode == 0 and r.stdout.strip():
            d = subprocess.run(['git', 'status', '--porcelain'], **opts)
            return r.stdout.strip() + ('+' if d.stdout.strip() else '')
    except (OSError, subprocess.SubprocessError):
        pass
    return source_fingerprint(root)[:7]


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


def _port_owner_pid(port: int):
    """PID of the process listening on 127.0.0.1:port, or None. Windows: netstat; POSIX: lsof."""
    import subprocess
    try:
        if os.name == 'nt':
            out = subprocess.run(['netstat', '-ano', '-p', 'tcp'], capture_output=True, text=True, timeout=15).stdout
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[0].upper() == 'TCP' and parts[1].endswith(f':{port}') and parts[3] == 'LISTENING':
                    return int(parts[4])
        else:
            out = subprocess.run(['lsof', '-t', f'-iTCP:{port}', '-sTCP:LISTEN'], capture_output=True, text=True, timeout=15).stdout
            return int(out.split()[0]) if out.split() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


def force_stop_instance(port: int, wait_s: float = 5.0) -> bool:
    """Kill the risk-monitor process holding `port` when it ignored the shutdown route
    (a copy started from code older than 2026-09-16 has no such route). Only called after
    `probe` confirmed the listener is a risk-monitor instance, never for another
    application. Returns True once the port is free."""
    import subprocess
    import time
    pid = _port_owner_pid(port)
    if pid is None or pid == os.getpid():
        return False
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True, timeout=15)
        else:
            os.kill(pid, 9)
    except (OSError, subprocess.SubprocessError):
        return False
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if probe(f'http://127.0.0.1:{port}') is None:
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
    # Root logger has no handler anywhere else in this app (checked 2026-09-16): without
    # this, every log.info/log.warning call in data/ingest/bnp.py and ui/uploads.py is
    # silently dropped (root defaults to WARNING with no handler -> nothing is printed).
    # One handler at the single entry point, stdout, so the terminal that ran
    # `2_launcher.py start` is a persistent record of anything logged app-wide.
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    args = parse_args([] if argv is None else argv)
    try:
        from ui.app import create_app, get_db_path
        import openpyxl
        import xlrd
    except ImportError as exc:
        print(f'Missing dependency: {exc.name}. Run: py -3 2_launcher.py setup')
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
                elif force_stop_instance(port):
                    print(f'Port {port}: force-stopped risk-monitor instance {seen} ({why}; it had no shutdown route).')
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
          f'Build:        {build_label()}  (fingerprint {fingerprint})\n'
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
