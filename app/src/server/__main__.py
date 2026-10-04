"""Uvicorn launcher for the Juno FastAPI server: ``python -m server``.

Host and port come from the ``server`` section of the YAML config (see
``juno.config``).
"""

import logging
import sys

import uvicorn
from dotenv import find_dotenv, load_dotenv

from juno.config import ConfigError, load_config
from juno.parent_watch import start_parent_watch


def main() -> int:
    try:
        config = load_config().server
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(level=logging.INFO,
                        format="%(name)s - %(levelname)s - %(message)s")
    start_parent_watch()
    # The annotation model's API key (GEMINI_API_KEY) may live in a .env file.
    load_dotenv(find_dotenv(usecwd=True))

    uvicorn.run("server.app:create_app", factory=True, host=config.host, port=config.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
