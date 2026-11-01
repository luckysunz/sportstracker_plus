"""Web service: serves the backend on the local network.

    python -m sportstracker.service_web --config config/config.json --port 8080

Uses the development server of Werkzeug with threads. That is sufficient for
one or two users on a home network and avoids an extra package on the Pi Zero.
The backend has no TLS; it is meant for the trusted home network only.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Optional

from .runtime import PROJECT_ROOT
from .webapp import create_app


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="sportstracker.service_web")
    parser.add_argument("--config", default=os.path.join(PROJECT_ROOT, "config", "config.json"))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from werkzeug.serving import run_simple

    app = create_app(args.config)
    run_simple(args.host, args.port, app, threaded=True, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
