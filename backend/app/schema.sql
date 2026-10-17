-- OpsDesk database schema.
-- Applied by `python -m app.migrate`. Every statement is idempotent (IF NOT EXISTS),
-- so running it again on an existing database is safe.

-- ---------------------------------------------------------------------------
-- Identity: users, teams, and a role per (user, team)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    email         TEXT        NOT NULL UNIQUE,
    name          TEXT        NOT NULL,
    password_hash TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS teams (
    id         BIGSERIAL PRIMARY KEY,
    name       TEXT        NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A user can be in many teams, with a different role in each.
CREATE TABLE IF NOT EXISTS memberships (
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    team_id BIGINT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    role    TEXT   NOT NULL CHECK (role IN ('viewer', 'member', 'lead')),
    PRIMARY KEY (user_id, team_id)
);
CREATE INDEX IF NOT EXISTS memberships_team_idx ON memberships (team_id);

-- ---------------------------------------------------------------------------
-- Work items
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS work_items (
    id          BIGSERIAL PRIMARY KEY,
    team_id     BIGINT      NOT NULL REFERENCES teams(id),
    title       TEXT        NOT NULL CHECK (char_length(title) BETWEEN 1 AND 200),
    description TEXT        NOT NULL DEFAULT '',
    type        TEXT        NOT NULL CHECK (type IN ('incident', 'customer_issue', 'payment', 'compliance', 'task')),
    priority    TEXT        NOT NULL CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
    status      TEXT        NOT NULL DEFAULT 'open'
                            CHECK (status IN ('open', 'in_progress', 'awaiting_approval', 'resolved', 'closed')),
    assignee_id BIGINT      REFERENCES users(id),
    created_by  BIGINT      NOT NULL REFERENCES users(id),
    due_date    DATE,
    -- Optimistic locking: every change increments this. Clients send the version
    -- they last saw; if it no longer matches, the change is rejected (409).
    version     INTEGER     NOT NULL DEFAULT 1,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Full-text search column, maintained by Postgres itself.
    search_vector TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english', title || ' ' || description)
    ) STORED
);

-- Indexes match the queries the dashboard actually runs (see items_repo.list_items).
CREATE INDEX IF NOT EXISTS work_items_team_updated_idx
    ON work_items (team_id, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS work_items_team_status_updated_idx
    ON work_items (team_id, status, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS work_items_assignee_status_idx
    ON work_items (assignee_id, status);
CREATE INDEX IF NOT EXISTS work_items_unassigned_urgent_idx
    ON work_items (team_id, updated_at DESC)
    WHERE assignee_id IS NULL AND priority IN ('high', 'urgent') AND status NOT IN ('resolved', 'closed');
CREATE INDEX IF NOT EXISTS work_items_search_idx
    ON work_items USING GIN (search_vector);

-- ---------------------------------------------------------------------------
-- Activity history (append-only). Comments are activity events too, so the
-- item page shows one single timeline.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS activity_events (
    id         BIGSERIAL PRIMARY KEY,
    item_id    BIGINT      NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
    actor_id   BIGINT      NOT NULL REFERENCES users(id),
    kind       TEXT        NOT NULL,   -- created, updated, claimed, released, assigned, status_changed, commented
    data       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS activity_item_idx ON activity_events (item_id, id DESC);

-- ---------------------------------------------------------------------------
-- Idempotency keys: remember the response to a request so a retried request
-- (double click, network retry) returns the same result instead of acting twice.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS idempotency_keys (
    user_id      BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key          TEXT        NOT NULL,
    request_hash TEXT        NOT NULL,
    status_code  INTEGER,
    response     JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, key)
);

-- ---------------------------------------------------------------------------
-- Transactional outbox: jobs are inserted in the SAME transaction as the change
-- that caused them, and a separate worker process runs them.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS jobs (
    id           BIGSERIAL PRIMARY KEY,
    kind         TEXT        NOT NULL,
    payload      JSONB       NOT NULL,
    status       TEXT        NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'failed')),
    attempts     INTEGER     NOT NULL DEFAULT 0,
    max_attempts INTEGER     NOT NULL DEFAULT 5,
    run_after    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS jobs_pending_idx ON jobs (run_after, id) WHERE status = 'pending';

-- In-app notifications produced by the worker.
-- UNIQUE (job_id, user_id) makes the job handler idempotent: running the same
-- job twice cannot create a duplicate notification.
CREATE TABLE IF NOT EXISTS notifications (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    item_id    BIGINT      REFERENCES work_items(id) ON DELETE CASCADE,
    job_id     BIGINT      NOT NULL REFERENCES jobs(id),
    message    TEXT        NOT NULL,
    read_at    TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (job_id, user_id)
);
CREATE INDEX IF NOT EXISTS notifications_user_idx ON notifications (user_id, id DESC);
