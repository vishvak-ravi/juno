"""Web search for the chat model, through the ``ddgs`` metasearch library (no API key)."""

from ddgs import DDGS
from ddgs.exceptions import DDGSException


class SearchError(Exception):
    """The search engines could not be asked, or had no result."""


def search_web(query: str, max_results: int) -> list[dict]:
    """The best results for ``query``, each a title, a url and a snippet of the page."""
    try:
        found = DDGS().text(query, max_results=max_results)
    except DDGSException as exc:
        raise SearchError(f"the search failed: {exc}")
    return [{"title": result.get("title", ""), "url": result.get("href", ""),
             "snippet": result.get("body", "")} for result in found]
