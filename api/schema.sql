-- One row per Claude API call, written whether the call succeeded or not.
--
-- The cache columns are here from day one even though nothing caches until
-- Phase 07: its gate is "watch cache_read_input_tokens go up", and a column
-- that already exists is one less migration between you and that number.
create extension if not exists vector;

create table if not exists calls (
    id                          bigserial primary key,
    created_at                  timestamptz not null default now(),
    endpoint                    text        not null,
    provider                    text        not null,
    model                       text        not null,
    issue_number                integer,
    input_tokens                integer     not null default 0,
    output_tokens               integer     not null default 0,
    cache_creation_input_tokens integer     not null default 0,
    cache_read_input_tokens     integer     not null default 0,
    latency_ms                  integer     not null,
    cost_usd                    numeric(12, 6) not null default 0,
    stop_reason                 text,
    request_id                  text,
    error                       text
);

create index if not exists calls_created_at_idx on calls (created_at desc);

-- Phase 03 --------------------------------------------------------------
-- One row per chunk of source. `strategy` lets v1 and v2 live side by side so
-- the comparison the gate demands needs no re-index and no second database.
create table if not exists chunks (
    id          bigserial primary key,
    strategy    text         not null,
    path        text         not null,
    start_line  integer      not null,
    end_line    integer      not null,
    text        text         not null,
    -- 768 = nomic-embed-text. A different embedding model means a different
    -- column, which is the honest signal that it is a different index.
    embedding   vector(768)  not null
);

create index if not exists chunks_strategy_idx on chunks (strategy);
-- Cosine, because the embeddings are not normalised to unit length by us and
-- cosine is what nomic-embed-text is trained for.
create index if not exists chunks_embedding_idx
    on chunks using hnsw (embedding vector_cosine_ops);
