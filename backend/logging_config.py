"""
Centralised logging setup.

Replaces scattered print() calls with a real logging framework:
- console output (so `flask run` still shows activity)
- rotating file logs under backend/logs/app.log (2 MB x 3 backups)

Call configure_logging(app) once at startup. Modules then use:
    import logging
    logger = logging.getLogger(__name__)
    logger.info("..."); logger.warning("..."); logger.exception("...")
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
_MARKER = '_qa_platform_handler'


class _SafeRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that never spews a '--- Logging error ---' traceback
    when rotation fails. On Windows the rename in doRollover throws
    PermissionError (WinError 32) whenever app.log is still held open — by the
    Flask debug reloader's second process, by handle-release lag right after the
    stream is closed, or by antivirus. The stdlib handler turns that into a noisy
    traceback on the console. We instead swallow it: re-open the current file and
    keep logging. Worst case the file grows a little past maxBytes until a later
    rollover succeeds — vastly better than dumping a traceback per log line.
    """
    def doRollover(self):
        try:
            super().doRollover()
        except OSError:
            try:
                if self.stream:
                    self.stream.close()
            except Exception:
                pass
            try:
                self.stream = self._open()   # resume appending to the existing file
            except Exception:
                self.stream = None


def configure_logging(app=None):
    debug = bool(app.config.get('DEBUG')) if app is not None else False
    level = logging.DEBUG if debug else logging.INFO

    fmt = logging.Formatter(
        '%(asctime)s %(levelname)-7s [%(name)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    root = logging.getLogger()
    root.setLevel(level)

    # Quiet noisy third-party loggers FIRST — these flood the terminal in DEBUG
    # mode (urllib3 logs every HEAD/GET, PIL logs every PNG chunk). Apply this
    # every time configure_logging() runs, not only when handlers are being
    # attached; the Werkzeug auto-reloader forks the process and our guard below
    # was skipping this on the reloaded child, leaving urllib3 chatty.
    for name in ('werkzeug', 'urllib3', 'urllib3.connectionpool', 'urllib3.util',
                 'PIL', 'PIL.PngImagePlugin', 'PIL.Image',
                 'asyncio', 'playwright', 'requests'):
        logging.getLogger(name).setLevel(logging.WARNING)

    # Guard against duplicate HANDLERS when the dev server reloads.
    if any(getattr(h, _MARKER, False) for h in root.handlers):
        return root

    # Force the console stream to UTF-8 and never crash on a stray byte.
    # On Windows the default console encoding is a legacy code page (cp1252),
    # so logging a crawled URL/title containing any non-ASCII char (₹, é, emoji,
    # percent-encoded junk) raised UnicodeEncodeError inside the handler — which
    # printed a "--- Logging error ---" traceback instead of the log line. The
    # file handler is already utf-8; this brings the console in line.
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass  # older/odd streams without reconfigure(); handler falls back below

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    setattr(console, _MARKER, True)
    root.addHandler(console)

    # Only ONE process should own app.log. Flask's debug reloader runs two
    # processes (a watcher supervisor + the child that actually serves), and
    # both execute this module-level config — so both opened app.log and the
    # rotation rename failed with WinError 32, printing a "--- Logging error ---"
    # traceback. The child sets WERKZEUG_RUN_MAIN=true; the supervisor doesn't.
    # In debug, give the file handler to the serving child only. Outside debug
    # (production / gunicorn) there's no reloader, so attach it normally.
    is_reloader_supervisor = debug and os.environ.get('WERKZEUG_RUN_MAIN') != 'true'
    if not is_reloader_supervisor:
        try:
            os.makedirs(_LOG_DIR, exist_ok=True)
            file_handler = _SafeRotatingFileHandler(
                os.path.join(_LOG_DIR, 'app.log'),
                maxBytes=2_000_000, backupCount=3, encoding='utf-8',
            )
            file_handler.setFormatter(fmt)
            setattr(file_handler, _MARKER, True)
            root.addHandler(file_handler)
        except Exception as e:  # never let logging setup crash the app
            root.warning("File logging disabled: %s", e)

    return root
