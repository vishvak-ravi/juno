"""Ranks the lattice's insights against a task, as Dawn picks the insights an action rests on.

``Reranker`` is what ``lattice.dawn`` ranks through. ``VoyageReranker`` is the
paper's cross encoder, Voyage AI's ``rerank`` endpoint (key VOYAGE_API_KEY);
``ModelReranker`` has a chat model score each document's relevance to the
query on its own, for a device without that key. ``reranker_for`` picks one
from ``dawn.rerank_model``: ``voyage/<model>`` or ``llm``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, Protocol

import voyageai
from voyageai.error import RateLimitError
from pydantic import BaseModel, Field

from lattice.llm import ModelCall
from lattice.parse import parse_model_json

logger = logging.getLogger(__name__)

VOYAGE_PREFIX = "voyage/"
MODEL_RERANKER = "llm"
# A call Voyage refuses for the rate limit is made again after this many
# seconds (its lowest tier allows 3 calls a minute), this many times in all.
RATE_LIMIT_WAIT_SECONDS = 21.0
RATE_LIMIT_ATTEMPTS = 6

RELEVANCE_PROMPT = """Rate how relevant the INSIGHT about a user is to the TASK the user is working on: how much it would change how a personal agent helps with the task.

[TASK]
{query}

[INSIGHT]
{document}

Return your results only in this exact JSON format:
{{"relevance": <0.0 to 1.0>}}
"""


class Ranked(BaseModel):
    # Index into the documents given.
    index: int
    score: float


class RelevanceReply(BaseModel):
    relevance: float = Field(ge=0.0, le=1.0)


class Reranker(Protocol):

    async def rank(self, query: str, documents: list[str], top_k: int) -> list[Ranked]:
        """The ``top_k`` documents most relevant to ``query``, best first."""


class VoyageReranker:

    def __init__(self, model: str, client: Optional[voyageai.AsyncClient] = None):
        self.model = model
        self.client = client

    async def rank(self, query: str, documents: list[str], top_k: int) -> list[Ranked]:
        if self.client is None:
            self.client = voyageai.AsyncClient()
        for attempt in range(1, RATE_LIMIT_ATTEMPTS + 1):
            try:
                ranking = await self.client.rerank(query, documents, model=self.model, top_k=top_k)
                return [Ranked(index=r.index, score=r.relevance_score) for r in ranking.results]
            except RateLimitError as exc:
                if attempt == RATE_LIMIT_ATTEMPTS:
                    raise
                logger.warning("voyage rate limit (attempt %d of %d); waiting %.0fs", attempt,
                               RATE_LIMIT_ATTEMPTS, RATE_LIMIT_WAIT_SECONDS)
                await asyncio.sleep(RATE_LIMIT_WAIT_SECONDS)
        raise AssertionError("unreachable")


class ModelReranker:

    def __init__(self, call: ModelCall, model: str):
        self.call = call
        self.model = model

    async def score(self, query: str, index: int, document: str) -> Ranked:
        reply = await self.call(self.model, RELEVANCE_PROMPT.format(query=query, document=document),
                                RelevanceReply.model_json_schema())
        return Ranked(index=index, score=RelevanceReply.model_validate(parse_model_json(reply.text)).relevance)

    async def rank(self, query: str, documents: list[str], top_k: int) -> list[Ranked]:
        scored = await asyncio.gather(*(self.score(query, i, d) for i, d in enumerate(documents)))
        return sorted(scored, key=lambda r: (-r.score, r.index))[:top_k]


def reranker_for(rerank_model: str, call: ModelCall, scoring_model: str) -> Reranker:
    """The reranker ``dawn.rerank_model`` names; ``scoring_model`` is the chat model ``llm`` uses."""
    if rerank_model.startswith(VOYAGE_PREFIX):
        return VoyageReranker(rerank_model[len(VOYAGE_PREFIX):])
    if rerank_model == MODEL_RERANKER:
        return ModelReranker(call, scoring_model)
    raise ValueError(f"dawn.rerank_model must be voyage/<model> or {MODEL_RERANKER}, not {rerank_model!r}")
