"""``/``: the phone client's page, and where the web app's files are.

The web app (``apps/web`` in the checkout) has no build step: the server serves
its files as they are. ``/`` is ``index.html``, one page that calls the routes
from the address it was opened at; ``/draw`` (``server.routes.draw``) is
``draw.html``; ``/src/`` is ``src/``, the modules the pages import, which the
desktop app's browser page (``apps/desktop/src/renderer/index.html``) also
imports.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

# packages/server/src/server/routes/web.py, five levels below the checkout.
WEB_DIR = Path(__file__).resolve().parents[5] / "apps" / "web"

router = APIRouter(tags=["web"])


@router.get("/", include_in_schema=False)
def get_web_page() -> FileResponse:
    # no-cache: a copy saved to the phone's Home Screen asks for the current page each time.
    return FileResponse(WEB_DIR / "index.html", media_type="text/html",
                        headers={"Cache-Control": "no-cache"})
