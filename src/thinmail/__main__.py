"""thinmail [--config FILE]: serve the web inbox (settings: see README)."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .web import Config, make_server


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="thinmail", description="Thin web inbox for 2FA codes")
    p.add_argument("--config", help="settings file (default: $THINMAIL_CONF, ~/.config/thinmail.conf, /etc/thinmail.conf)")
    p.add_argument("--version", action="version", version=f"thinmail {__version__}")
    args = p.parse_args(argv)
    config = Config.load(args.config)
    server = make_server(config)
    print(f"thinmail {__version__} on http://{config.listen_host}:{config.listen_port} "
          f"(IMAP {config.imap.host}:{config.imap.port}, {config.imap.security})", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
