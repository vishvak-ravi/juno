"""Uvicorn launcher for the Juno FastAPI server: ``python -m server``.

It listens on JUNO_SERVER_HOST and JUNO_SERVER_PORT and reads its settings from
``<data_dir>/settings.json`` (see ``juno.config``).
"""

import logging
import sys

import uvicorn
from dotenv import find_dotenv, load_dotenv

from juno.config import ConfigError, server_host, server_port
from juno.settings import server_settings


def main() -> int:
    # This machine's settings and the models' API keys may live in a .env file.
    load_dotenv(find_dotenv(usecwd=True))
    try:
        port = server_port()
        server_settings().read()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(level=logging.INFO,
                        format="%(name)s - %(levelname)s - %(message)s")
    uvicorn.run("server.app:create_app", factory=True, host=server_host(), port=port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
