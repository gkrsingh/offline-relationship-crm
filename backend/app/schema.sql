-- ---------------------------------------------------------------------------
-- AI-native relationship CRM -- SQLite schema
--
-- Layering rule: `people` holds records exactly as ingested and is never
-- rewritten. Everything downstream (normalization, dedupe, enrichment,
-- scoring, intros) is an additive table keyed by person_id, so any stage can
-- be recomputed without losing source data and the UI can always show the
-- source value next to the derived value.
-- ---------------------------------------------------------------------------

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Stage 0: raw ingest
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS people (
    id            TEXT PRIMARY KEY,
    full_name     TEXT,
    email         TEXT,
    linkedin_url  TEXT,
    company       TEXT,
    title         TEXT,
    location      TEXT,
    bio           TEXT,
    source        TEXT,
    needs         TEXT,               -- JSON array of strings
    offers        TEXT,               -- JSON array of strings
    created_at    TEXT,               -- as supplied by the source system
    ingested_at   TEXT NOT NULL,

    -- Set once a human approves a merge in the duplicate-review queue.
    -- NULL means this record is its own canonical person.
    merged_into   TEXT REFERENCES people(id)
);

CREATE INDEX IF NOT EXISTS idx_people_merged_into ON people(merged_into);

-- ---------------------------------------------------------------------------
-- Stage 1: deterministic normalization (no AI)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS people_normalized (
    person_id          TEXT PRIMARY KEY REFERENCES people(id) ON DELETE CASCADE,
    name_normalized    TEXT,
    name_first         TEXT,
    name_last          TEXT,
    email_normalized   TEXT,          -- lowercased, gmail dots and +tags folded
    email_local        TEXT,          -- local part, used as a blocking key
    email_domain       TEXT,
    email_is_personal  INTEGER,       -- 1 for gmail/outlook/proton and friends
    linkedin_handle    TEXT,          -- slug only, protocol and params stripped
    company_normalized TEXT,          -- legal suffixes and punctuation stripped
    title_normalized   TEXT,          -- abbreviations expanded
    title_canonical    TEXT,          -- collapsed to a role token
    location_city      TEXT,
    location_country   TEXT,
    needs_text         TEXT,          -- needs joined into one string
    offers_text        TEXT,          -- offers joined into one string
    name_tokens        TEXT,          -- JSON array, sorted token set
    completeness       REAL,          -- 0..1, deterministic
    missing_fields     TEXT,          -- JSON array of field names
    is_blocked         INTEGER NOT NULL DEFAULT 0,
    blocked_reason     TEXT,          -- plain English, not a percentage
    normalized_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_norm_email ON people_normalized(email_normalized);
CREATE INDEX IF NOT EXISTS idx_norm_li    ON people_normalized(linkedin_handle);
CREATE INDEX IF NOT EXISTS idx_norm_name  ON people_normalized(name_normalized);

-- ---------------------------------------------------------------------------
-- Stage 2/3: duplicate detection
-- One row per candidate pair. The writer enforces person_a_id < person_b_id so
-- a pair can never be stored twice under two orderings.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS duplicate_pairs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    person_a_id  TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    person_b_id  TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,

    stage        TEXT NOT NULL,       -- deterministic | fuzzy | llm
    method       TEXT NOT NULL,       -- exact_email | exact_linkedin | fuzzy_name | ...
    score        REAL,                -- fuzzy score 0..100, NULL for deterministic

    verdict      TEXT NOT NULL,       -- same_person | different_people | insufficient_evidence
    confidence   REAL,
    reason       TEXT,
    llm_used     INTEGER NOT NULL DEFAULT 0,

    -- auto_merged | pending | rejected. insufficient_evidence is always pending.
    review_state  TEXT NOT NULL DEFAULT 'pending',
    blocking_keys TEXT,               -- JSON array of the keys that surfaced the pair

    created_at   TEXT NOT NULL,
    UNIQUE (person_a_id, person_b_id)
);

CREATE INDEX IF NOT EXISTS idx_dup_verdict ON duplicate_pairs(verdict);
CREATE INDEX IF NOT EXISTS idx_dup_state   ON duplicate_pairs(review_state);

-- Human decisions from the review queue. Kept separate from the machine
-- verdict so we never overwrite what the pipeline thought.
CREATE TABLE IF NOT EXISTS duplicate_reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_id     INTEGER NOT NULL REFERENCES duplicate_pairs(id) ON DELETE CASCADE,
    decision    TEXT NOT NULL,        -- merge | keep_both | not_sure
    note        TEXT,
    decided_at  TEXT NOT NULL,
    UNIQUE (pair_id)
);

-- A merged cluster. Source rows are never deleted, so setting reverted_at and
-- clearing people.merged_into restores the originals exactly.
CREATE TABLE IF NOT EXISTS merge_groups (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    source_record_ids   TEXT NOT NULL,   -- JSON array, always includes the canonical id
    decided_by          TEXT NOT NULL,   -- stage1_exact | stage2_fuzzy | llm | human
    status              TEXT NOT NULL,   -- merged | pending_review
    resolved            TEXT NOT NULL,   -- JSON: the survivorship result
    provenance          TEXT NOT NULL,   -- JSON: field -> source record id
    conflicts           TEXT,            -- JSON array, empty when clean
    first_contact_at    TEXT,
    created_at          TEXT NOT NULL,
    reverted_at         TEXT,
    UNIQUE (canonical_person_id)
);

-- ---------------------------------------------------------------------------
-- Stage 4: AI enrichment. One row per person, always attributable.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS enrichment (
    person_id      TEXT PRIMARY KEY REFERENCES people(id) ON DELETE CASCADE,
    persona        TEXT,              -- founder|operator|investor|service_provider|ic|unknown
    seniority      TEXT,
    company_stage  TEXT,
    sector         TEXT,
    geography      TEXT,
    needs          TEXT,              -- JSON array
    offers         TEXT,              -- JSON array
    confidence     REAL,
    evidence       TEXT,              -- JSON: [{field, quote, supports}]

    -- Every quote is checked against the field it claims to come from. A record
    -- where these disagree had its justification fabricated, which is worth
    -- knowing even when the classification happens to be right.
    evidence_total      INTEGER NOT NULL DEFAULT 0,
    evidence_verified   INTEGER NOT NULL DEFAULT 0,
    evidence_unverified TEXT,
    low_confidence      INTEGER NOT NULL DEFAULT 0,

    provider       TEXT,
    model          TEXT,
    prompt_version TEXT,
    from_cache     INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_enrich_persona ON enrichment(persona);

-- ---------------------------------------------------------------------------
-- Stage 5: applicants
-- ---------------------------------------------------------------------------
-- A membership application. Offline vets people to join the network; this is
-- not a job application, so the fields describe what someone is building and
-- what they would bring, not what role they want.
CREATE TABLE IF NOT EXISTS applications (
    person_id     TEXT PRIMARY KEY REFERENCES people(id) ON DELETE CASCADE,
    building_now  TEXT,          -- company, stage and traction, in their words
    why_join      TEXT,
    contribution  TEXT,          -- what they would give the network
    referred_by   TEXT REFERENCES people(id),   -- an existing member, or NULL
    submitted_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_app_referred_by ON applications(referred_by);

-- Score is computed by deterministic code. The LLM only writes `explanation`.
CREATE TABLE IF NOT EXISTS applicant_scores (
    person_id       TEXT PRIMARY KEY REFERENCES people(id) ON DELETE CASCADE,
    persona_fit     REAL NOT NULL,  -- out of 30
    seniority       REAL NOT NULL,  -- out of 20
    company_stage   REAL NOT NULL,  -- out of 20
    referral_signal REAL NOT NULL,  -- out of 15
    profile_signal  REAL NOT NULL,  -- out of 15
    total           REAL NOT NULL,  -- out of 100
    band            TEXT NOT NULL,  -- strong >= 75 | review 55-74 | weak < 55
    signals             TEXT,           -- JSON: which rubric signals fired, per component
    explanation         TEXT,           -- LLM prose, generated FROM the numbers above
    bullets             TEXT,           -- JSON array, 3-4 items
    explanation_kind    TEXT,           -- why | why_not

    -- Numbers the prose used that the breakdown never supplied. Should always
    -- be empty: the model is shown the breakdown and nothing else. Recorded
    -- because "should" is not a guarantee and the check costs one regex.
    unsupported_numbers TEXT,
    created_at          TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Stage 6: introduction engine
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS embeddings (
    person_id  TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,        -- needs | offers | profile
    vector     BLOB NOT NULL,        -- float32 array
    dim        INTEGER NOT NULL,
    model      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (person_id, kind)
);

CREATE TABLE IF NOT EXISTS introductions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    person_a_id     TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    person_b_id     TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,

    score           REAL NOT NULL,   -- final blended score 0..1
    complementarity REAL,            -- A.needs <-> B.offers cosine
    similarity      REAL,            -- profile <-> profile cosine
    matched_need    TEXT,            -- the specific need that matched
    matched_offer   TEXT,            -- the specific offer that matched

    why             TEXT,
    a_gets          TEXT,
    b_gets          TEXT,
    draft_message   TEXT,

    status          TEXT NOT NULL DEFAULT 'suggested', -- suggested|approved|dismissed
    created_at      TEXT NOT NULL,
    decided_at      TEXT,
    UNIQUE (person_a_id, person_b_id)
);

CREATE INDEX IF NOT EXISTS idx_intro_status ON introductions(status);
CREATE INDEX IF NOT EXISTS idx_intro_a      ON introductions(person_a_id, score);

-- Never suggest this pair. Checked by the deterministic safety filter before
-- any intro is generated. Stored with person_a_id < person_b_id.
CREATE TABLE IF NOT EXISTS blocked_pairs (
    person_a_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    person_b_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    reason      TEXT,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (person_a_id, person_b_id)
);

-- ---------------------------------------------------------------------------
-- Cross-cutting: run accounting.
-- The LLM cache is NOT here. It lives in data/cache/llm as one JSON file per
-- key, so cached responses are reviewable in a diff and ship with the repo.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    stage       TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    llm_calls   INTEGER NOT NULL DEFAULT 0,
    cache_hits  INTEGER NOT NULL DEFAULT 0,
    records_in  INTEGER,
    records_out INTEGER,
    notes       TEXT
);
