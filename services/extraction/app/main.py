import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from app.embeddings import warmup, embed_passage, embed_query
from app.extractors.article import extract_article
from app.extractors.instagram import extract_instagram
from app.extractors.pdf import extract_pdf
from app.extractors.youtube import extract_youtube
from app.linker import link_source
from app.processor import process_source
from app.searcher import search as _search
from app.synthesizer import synthesize_digest
from app.schemas import (
    DigestRequest,
    DigestResponse,
    EmbedBatchRequest,
    EmbedBatchResponse,
    EmbedRequest,
    EmbedResponse,
    ExtractRequest,
    ExtractResponse,
    LinkRequest,
    LinkResponse,
    ProcessRequest,
    ProcessResponse,
    SearchRequest,
    SearchResponse,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("extraction")


@asynccontextmanager
async def lifespan(app: FastAPI):
    warmup()
    yield


app = FastAPI(title="Media Pulse - Extraction Service", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest) -> EmbedResponse:
    vec = embed_query(req.text) if req.mode == "query" else embed_passage(req.text)
    return EmbedResponse(embedding=vec, dims=len(vec))


@app.post("/embed-batch", response_model=EmbedBatchResponse)
def embed_batch(req: EmbedBatchRequest) -> EmbedBatchResponse:
    fn = embed_query if req.mode == "query" else embed_passage
    vecs = [fn(t) for t in req.texts]
    return EmbedBatchResponse(embeddings=vecs, dims=len(vecs[0]) if vecs else 0)


@app.post("/link", response_model=LinkResponse)
def link(req: LinkRequest) -> LinkResponse:
    # Linking is non-critical — degrade gracefully so a Gemini blip never
    # re-marks an already-completed source as failed in n8n.
    try:
        result = link_source(req.source_id)
        return LinkResponse(**result)
    except Exception as exc:
        logger.exception("Linking failed for source %s (non-critical)", req.source_id)
        return LinkResponse(source_id=req.source_id, connections_written=0)


@app.post("/digest", response_model=DigestResponse)
def digest(req: DigestRequest) -> DigestResponse:
    try:
        result = synthesize_digest(req.period_type, req.since, req.until)
        return DigestResponse(**result)
    except Exception as exc:
        logger.exception("Digest synthesis failed")
        raise HTTPException(status_code=502, detail=f"Digest failed: {exc}") from exc


@app.post("/process", response_model=ProcessResponse)
def process(req: ProcessRequest) -> ProcessResponse:
    try:
        result = process_source(req.source_id)
        return ProcessResponse(**result)
    except Exception as exc:
        logger.exception("Processing failed for source %s", req.source_id)
        raise HTTPException(status_code=502, detail=f"Processing failed: {exc}") from exc


@app.post("/search", response_model=SearchResponse)
def search(req: SearchRequest) -> SearchResponse:
    try:
        result = _search(req.query, req.match_count)
        return SearchResponse(**result)
    except Exception as exc:
        logger.exception("Search failed for query: %s", req.query)
        raise HTTPException(status_code=502, detail=f"Search failed: {exc}") from exc


@app.post("/extract", response_model=ExtractResponse)
def extract(req: ExtractRequest) -> ExtractResponse:
    try:
        if req.content_type == "youtube" or "youtube.com" in req.url or "youtu.be" in req.url:
            return extract_youtube(req.url)
        # Route instagram.com URLs to the Instagram extractor regardless of content_type —
        # trafilatura only gets the login wall for these URLs.
        if req.content_type == "instagram" or "instagram.com" in req.url:
            return extract_instagram(req.url)
        if req.content_type in ("article", "tweet"):
            return extract_article(req.url)
        if req.content_type == "pdf" or req.url.lower().endswith(".pdf") or "/pdf/" in req.url.lower():
            return extract_pdf(req.url)
        raise HTTPException(
            status_code=501,
            detail=f"Extraction for content_type '{req.content_type}' is not implemented yet",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Extraction failed for %s", req.url)
        raise HTTPException(status_code=502, detail=f"Extraction failed: {exc}") from exc
