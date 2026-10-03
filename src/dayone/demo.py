"""Start the processing server (port 8100) and the phone simulator (port 8000) together.

    uv run python -m dayone.demo            # then open http://127.0.0.1:8000
    uv run python -m dayone.demo --offline  # the phone starts without network
    uv run python -m dayone.demo --reset    # wipe the demo databases first
"""

from __future__ import annotations

import argparse
import logging
import shutil
import threading
import time
from pathlib import Path

import httpx
import uvicorn

from dayone.device.app import Device
from dayone.device.app import create_app as create_device_app
from dayone.device.sync import Network
from dayone.server.app import create_app as create_server_app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--reset", action="store_true", help="delete the demo phone and server databases first")
    ap.add_argument("--dir", type=Path, default=Path("artifacts/demo"))
    args = ap.parse_args()
    if args.reset and args.dir.exists():
        shutil.rmtree(args.dir)
    server = uvicorn.Server(uvicorn.Config(create_server_app(args.dir / "server"), host="127.0.0.1", port=8100,
                                           log_level="warning"))
    threading.Thread(target=server.run, daemon=True, name="server").start()
    for _ in range(50):
        try:
            if httpx.get("http://127.0.0.1:8100/v1/health", timeout=0.5).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.2)
    device = Device(args.dir / "phone", "2468", "sf-amina", "token-sf-amina", "http://127.0.0.1:8100",
                    Network(online=not args.offline))
    print("\n  DayOne demo ready:  phone  -> http://127.0.0.1:8000\n"
          "                      server -> http://127.0.0.1:8100/dashboard\n")
    uvicorn.run(create_device_app(device), host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    main()
