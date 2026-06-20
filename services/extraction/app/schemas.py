from typing import Literal, Optional

from pydantic import BaseModel, Field


class ExtractRequest(BaseModel):
    url: str
    content_type: Literal["youtube", "article", "podcast", "pdf", "tweet", "instagram", "other"]


class ExtractResponse(BaseModel):
    """Contract consumed by n8n. Keep this stable across Phase 1 -> Phase 2
    so swapping the implementation behind /extract never requires
    changing the n8n workflow."""

    title: Optional[str] = None
    author: Optional[str] = None
    raw_content: str
    metadata: dict = Field(default_factory=dict)


class EmbedRequest(BaseModel):
    text: str
    mode: Literal["passage", "query"] = "passage"


class EmbedResponse(BaseModel):
    embedding: list[float]
    dims: int


class EmbedBatchRequest(BaseModel):
    texts: list[str]
    mode: Literal["passage", "query"] = "passage"


class EmbedBatchResponse(BaseModel):
    embeddings: list[list[float]]
    dims: int


class ProcessRequest(BaseModel):
    source_id: str


class ProcessResponse(BaseModel):
    source_id: str
    summary: str
    chunks_written: int
    insights_written: int
    tags_written: int = 0


class SearchRequest(BaseModel):
    query: str
    match_count: int = 5


class SearchResponse(BaseModel):
    query: str
    answer: str
    sources: list[dict]


class LinkRequest(BaseModel):
    source_id: str


class LinkResponse(BaseModel):
    source_id: str
    connections_written: int


class DigestRequest(BaseModel):
    period_type: Literal["daily", "weekly", "monthly"] = "daily"
    since: str   # ISO-8601 datetime
    until: str   # ISO-8601 datetime


class DigestResponse(BaseModel):
    period_type: str
    sources_included: int
    digest_id: Optional[str] = None
    skipped: bool = False
