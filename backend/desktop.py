"""Desktop / packaged entry point for ROMLoom.

This is what the PyInstaller build runs (see romloom.spec). It starts the
uvicorn server and opens the browser, so the Windows .exe is double-click-run.
Configuration comes from romloom.toml next to the executable, or ROMLOOM_* env
vars — see app.config.
"""
from __future__ import annotations

import threading
import time
import webbrowser

import uvicorn

from app import config
from app.main import app


def _open_browser(url: str) -> None:
    # Give uvicorn a moment to bind before the browser hits the port.
    time.sleep(1.5)
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main() -> None:
    host = config.HTTP_HOST
    port = config.HTTP_PORT
    # 0.0.0.0 / :: aren't browsable addresses — show loopback in the URL.
    url_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{url_host}:{port}"

    print("ROMLoom")
    print(f"  opening : {url}")
    print(f"  library : {config.ROMS_ROOT}")
    print(f"  output  : {config.OUTPUT_ROOT}")
    print(f"  config  : {config.CONFIG_DIR}")
    if not config.ROMS_ROOT.exists():
        print("  ! library folder not found. Set 'roms_root' in romloom.toml "
              "(next to this program) or the ROMLOOM_ROMS_ROOT env var, then "
              "restart.")
    print("  (close this window to quit)")

    threading.Thread(target=_open_browser, args=(url,), daemon=True).start()
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
