# Backend: API server, accounts, billing, and shared infrastructure.
#
# .env is loaded here, before any submodule runs. Several modules read
# os.getenv at import time; loading it only in shared/config.py made each of
# those depend on config happening to be imported first. Real environment
# variables still win, so container and systemd settings override the file.
from pathlib import Path as _Path

try:
    from dotenv import load_dotenv as _load_dotenv

    _load_dotenv(_Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:  # pragma: no cover - dotenv is an optional convenience
    pass
