-- The whole schema. Applied at every start-up, so each statement must be safe to run again.

-- --- Conversations and messages ---

CREATE TABLE IF NOT EXISTS conversations (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_id         text UNIQUE,
    customer_id       text,
    customer_metadata jsonb,
    busy_turn         uuid,
    busy_until        timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id uuid NOT NULL REFERENCES conversations (id),
    -- the sender's id, else a hash of author, time and body
    message_key     text NOT NULL,
    sender_id       text,
    author          text NOT NULL CHECK (author IN ('customer', 'support_agent', 'operator', 'assistant')),
    body            text NOT NULL,
    sent_at         timestamptz NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, message_key)
);

-- --- Turns, steps and decisions ---

CREATE TABLE IF NOT EXISTS turns (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL REFERENCES conversations (id),
    kind            text NOT NULL CHECK (kind IN ('ingest', 'operator')),
    status          text NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'abandoned')),
    last_message_id bigint REFERENCES messages (id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz
);

CREATE TABLE IF NOT EXISTS steps (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    turn_id       uuid NOT NULL REFERENCES turns (id),
    kind          text NOT NULL CHECK (kind IN ('model', 'tool')),
    name          text NOT NULL,
    arguments     jsonb,
    result        jsonb,
    ok            boolean NOT NULL,
    duration_ms   integer NOT NULL,
    input_tokens  integer,
    output_tokens integer,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- append-only: a decision is never updated
CREATE TABLE IF NOT EXISTS decisions (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id uuid NOT NULL REFERENCES conversations (id),
    turn_id         uuid NOT NULL REFERENCES turns (id),
    version         integer NOT NULL,
    body            jsonb NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, version)
);

-- --- Mock providers: fixtures and charges ---

-- provider state of the mocks: read-only account and status-page fixtures, and the charges
CREATE TABLE IF NOT EXISTS mock_fixtures (
    kind text NOT NULL,
    key  text NOT NULL,
    body jsonb NOT NULL,
    PRIMARY KEY (kind, key)
);

CREATE TABLE IF NOT EXISTS mock_charges (
    charge_id    text PRIMARY KEY,
    customer_id  text NOT NULL,
    amount_cents integer NOT NULL,
    currency     text NOT NULL,
    state        text NOT NULL CHECK (state IN ('authorized', 'captured', 'failed', 'refunded')),
    created_at   timestamptz NOT NULL
);

-- --- Knowledge base ---

-- the knowledge base, with the search vector built from title and body
CREATE TABLE IF NOT EXISTS kb_articles (
    id         text PRIMARY KEY,
    title      text NOT NULL,
    body       text NOT NULL,
    updated_at timestamptz NOT NULL,
    search     tsvector GENERATED ALWAYS AS (to_tsvector('english', title || ' ' || body)) STORED
);

CREATE INDEX IF NOT EXISTS kb_articles_search ON kb_articles USING gin (search);

-- --- Mock providers: the refund on a charge ---

-- a charge carries its refund and the idempotency key that made it
ALTER TABLE mock_charges ADD COLUMN IF NOT EXISTS refund_id text;
ALTER TABLE mock_charges ADD COLUMN IF NOT EXISTS refund_key text;
ALTER TABLE mock_charges ADD COLUMN IF NOT EXISTS refund_amount_cents integer;

-- --- Actions and their events ---

-- every effect proposed, moved through one state machine by compare-and-set updates on status
CREATE TABLE IF NOT EXISTS actions (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind         text NOT NULL,
    -- the thing acted on
    key          text NOT NULL,
    -- copied from the policy row: whether the key stays taken after success and after a rejection
    hold_key     boolean NOT NULL,
    payload      jsonb NOT NULL,
    -- what the provider said about the thing when the action was proposed
    evidence     jsonb NOT NULL,
    status       text NOT NULL CHECK (status IN (
        'pending_approval', 'approved', 'rejected', 'stale', 'executing', 'succeeded', 'failed', 'needs_review'
    )),
    provider_ref text,
    claimed_at   timestamptz,
    attempts     integer NOT NULL DEFAULT 0,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- at most one action holds a key
CREATE UNIQUE INDEX IF NOT EXISTS actions_live_key ON actions (key)
 WHERE status IN ('pending_approval', 'approved', 'executing', 'needs_review')
    OR (hold_key AND status IN ('succeeded', 'rejected'));

-- append-only: every status change and every attempt, and every conversation that touched the action
CREATE TABLE IF NOT EXISTS action_events (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    action_id       uuid NOT NULL REFERENCES actions (id),
    type            text NOT NULL CHECK (type IN (
        'proposed', 'joined', 'approved', 'rejected', 'stale', 'claimed', 'provider_result', 'parked', 'resolved'
    )),
    actor           text NOT NULL,
    conversation_id uuid REFERENCES conversations (id),
    detail          jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS action_events_action ON action_events (action_id);
CREATE INDEX IF NOT EXISTS action_events_conversation ON action_events (conversation_id);

-- --- Mock providers: incidents ---

-- the incident system's own state: at most one open incident per dedup key
CREATE TABLE IF NOT EXISTS mock_incidents (
    incident_id text PRIMARY KEY,
    dedup_key   text NOT NULL,
    component   text NOT NULL,
    region      text NOT NULL,
    summary     text NOT NULL,
    status      text NOT NULL CHECK (status IN ('open', 'resolved')),
    opened_at   timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS mock_incidents_open ON mock_incidents (dedup_key) WHERE status = 'open';
