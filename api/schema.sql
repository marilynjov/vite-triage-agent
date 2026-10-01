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
