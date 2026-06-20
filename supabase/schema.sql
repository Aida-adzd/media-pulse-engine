-- =========================================================
-- The Media Pulse — Supabase schema (pgvector)
-- =========================================================

create extension if not exists vector;
create extension if not exists pgcrypto;

-- ---------------------------------------------------------
-- sources: one row per ingested item (URL forwarded via Telegram)
-- ---------------------------------------------------------
create table sources (
    id              uuid primary key default gen_random_uuid(),
    telegram_message_id bigint,
    chat_id         bigint,
    url             text,
    content_type    text not null check (content_type in ('youtube','article','podcast','pdf','tweet','instagram','other')),
    title           text,
    author          text,
    status          text not null default 'pending'
                        check (status in ('pending','fetching','processing','completed','failed')),
    error_message   text,
    raw_content     text,          -- transcript / article body
    summary         text,          -- generated short summary
    metadata        jsonb default '{}'::jsonb,  -- duration, published_at, thumbnail, word_count, etc.
    created_at      timestamptz default now(),
    processed_at    timestamptz
);

create index idx_sources_status on sources(status);
create index idx_sources_content_type on sources(content_type);
create index idx_sources_metadata on sources using gin(metadata);

-- ---------------------------------------------------------
-- chunks: text segments + embeddings for RAG
-- (vector dim assumes Gemini text-embedding-004 = 768; adjust if using a different model)
-- ---------------------------------------------------------
create table chunks (
    id           uuid primary key default gen_random_uuid(),
    source_id    uuid not null references sources(id) on delete cascade,
    chunk_index  int not null,
    content      text not null,
    embedding    vector(1024),  -- intfloat/e5-large-v2 (passage: prefix, normalized)
    token_count  int,
    created_at   timestamptz default now()
);

create index idx_chunks_source on chunks(source_id);
create index idx_chunks_embedding on chunks using hnsw (embedding vector_cosine_ops);

-- ---------------------------------------------------------
-- insights: structured extractions (key points, action items, entities, quotes)
-- ---------------------------------------------------------
create table insights (
    id            uuid primary key default gen_random_uuid(),
    source_id     uuid not null references sources(id) on delete cascade,
    insight_type  text not null check (insight_type in ('key_point','action_item','entity','quote','question')),
    content       text not null,
    metadata      jsonb default '{}'::jsonb,
    created_at    timestamptz default now()
);

create index idx_insights_source on insights(source_id);
create index idx_insights_type on insights(insight_type);

-- ---------------------------------------------------------
-- tags
-- ---------------------------------------------------------
create table tags (
    id    uuid primary key default gen_random_uuid(),
    name  text unique not null
);

create table source_tags (
    source_id  uuid references sources(id) on delete cascade,
    tag_id     uuid references tags(id) on delete cascade,
    primary key (source_id, tag_id)
);

-- ---------------------------------------------------------
-- connections: relationships discovered between sources (Linker agent output)
-- ---------------------------------------------------------
create table connections (
    id                 uuid primary key default gen_random_uuid(),
    source_id_a        uuid not null references sources(id) on delete cascade,
    source_id_b        uuid not null references sources(id) on delete cascade,
    relationship_type  text default 'related'
                           check (relationship_type in ('related','contradicts','builds_on','duplicate')),
    similarity_score   float,
    created_at         timestamptz default now(),
    unique (source_id_a, source_id_b)
);

create index idx_connections_a on connections(source_id_a);
create index idx_connections_b on connections(source_id_b);

-- ---------------------------------------------------------
-- digests: periodic "Pulse" synthesis (Synthesizer agent output)
-- ---------------------------------------------------------
create table digests (
    id            uuid primary key default gen_random_uuid(),
    period_type   text not null check (period_type in ('daily','weekly','monthly')),
    period_start  timestamptz not null,
    period_end    timestamptz not null,
    content       text not null,         -- generated digest markdown
    source_ids    uuid[] default '{}',   -- items covered in this digest
    created_at    timestamptz default now()
);

create index idx_digests_period on digests(period_start, period_end);

-- ---------------------------------------------------------
-- match_chunks: semantic search RPC used by the FastMCP server
-- ---------------------------------------------------------
create or replace function match_chunks(
    query_embedding vector(1024),
    match_count int default 10,
    filter_content_type text default null
)
returns table (
    chunk_id      uuid,
    source_id     uuid,
    content       text,
    similarity    float,
    title         text,
    url           text,
    content_type  text
)
language sql stable
as $$
    select
        c.id as chunk_id,
        c.source_id,
        c.content,
        1 - (c.embedding <=> query_embedding) as similarity,
        s.title,
        s.url,
        s.content_type
    from chunks c
    join sources s on s.id = c.source_id
    where filter_content_type is null or s.content_type = filter_content_type
    order by c.embedding <=> query_embedding
    limit match_count;
$$;
