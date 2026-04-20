-- schema.sql — Evidence database schema for investigative-pipeline
-- SQLite 3.x
--
-- Tables:
--   source          — bibliographic records
--   evidence        — individual factual claims
--   citation        — evidence → source links (many-to-many)
--   entity          — people, places, organizations
--   evidence_entity — evidence → entity links
--   thread          — interpretive themes / narrative threads
--   evidence_thread — evidence → thread links
--   packet          — curated research packets (collections of evidence)
--   packet_evidence — packet → evidence links
--   evidence_tag    — free-form tags on evidence
--   media           — images, video, audio, documents
--   media_segment   — timestamped segments within media
--   citation_media  — citation → media links

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── Sources ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS source (
    id                 TEXT    PRIMARY KEY,
    title              TEXT    NOT NULL,
    author             TEXT    DEFAULT 'Unknown',
    year               INTEGER,
    source_type        TEXT    DEFAULT 'book',  -- book|article|document|deposition|transcript
    certainty_base     INTEGER DEFAULT 7,
    is_memoir          INTEGER DEFAULT 0,       -- 0|1 boolean
    extraction_status  TEXT    DEFAULT 'pending',  -- pending|complete
    facts_extracted    INTEGER DEFAULT 0,
    vault_path         TEXT,
    notes              TEXT    DEFAULT '',
    updated            TEXT
);

-- ── Evidence ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS evidence (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    legacy_id      TEXT    UNIQUE,           -- original fact ID from JSONL (e.g. F-2005-0042)
    claim          TEXT    NOT NULL,
    date_value     TEXT,                     -- ISO date or year string
    date_precision TEXT    DEFAULT 'year',   -- exact|month|year|range|undated|approximate
    certainty      INTEGER DEFAULT 5,        -- 0–10
    status         TEXT    DEFAULT 'alleged',-- alleged|confirmed|disputed|disproven
    review_status  TEXT    DEFAULT 'draft',  -- draft|reviewed|final
    notes          TEXT    DEFAULT '',
    created        TEXT,
    modified       TEXT
);

CREATE INDEX IF NOT EXISTS idx_evidence_date     ON evidence(date_value);
CREATE INDEX IF NOT EXISTS idx_evidence_certainty ON evidence(certainty);
CREATE INDEX IF NOT EXISTS idx_evidence_status    ON evidence(status);

-- ── Citations ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS citation (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    evidence_id INTEGER NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
    source_id   TEXT    NOT NULL REFERENCES source(id)   ON DELETE RESTRICT,
    quote       TEXT    DEFAULT '',
    page        TEXT    DEFAULT '',
    UNIQUE(evidence_id, source_id, page)
);

CREATE INDEX IF NOT EXISTS idx_citation_evidence ON citation(evidence_id);
CREATE INDEX IF NOT EXISTS idx_citation_source   ON citation(source_id);

-- ── Entities ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS entity (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    entity_type TEXT    DEFAULT 'person',  -- person|place|org
    notes       TEXT    DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_entity_name ON entity(name);
CREATE INDEX IF NOT EXISTS idx_entity_type ON entity(entity_type);

-- ── Evidence → Entity ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS evidence_entity (
    evidence_id INTEGER NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
    entity_id   INTEGER NOT NULL REFERENCES entity(id)   ON DELETE CASCADE,
    PRIMARY KEY (evidence_id, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_evidence_entity_entity ON evidence_entity(entity_id);

-- ── Threads ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS thread (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    slug     TEXT    NOT NULL UNIQUE,
    title    TEXT    NOT NULL,
    keywords TEXT    DEFAULT '',  -- comma-separated keywords for auto-bucketing
    notes    TEXT    DEFAULT ''
);

-- ── Evidence → Thread ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS evidence_thread (
    evidence_id INTEGER NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
    thread_id   INTEGER NOT NULL REFERENCES thread(id)   ON DELETE CASCADE,
    PRIMARY KEY (evidence_id, thread_id)
);

CREATE INDEX IF NOT EXISTS idx_evidence_thread_thread ON evidence_thread(thread_id);

-- ── Packets ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS packet (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    description TEXT    DEFAULT '',
    created     TEXT,
    modified    TEXT
);

-- ── Packet → Evidence ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS packet_evidence (
    packet_id   INTEGER NOT NULL REFERENCES packet(id)   ON DELETE CASCADE,
    evidence_id INTEGER NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
    sort_order  INTEGER DEFAULT 0,
    PRIMARY KEY (packet_id, evidence_id)
);

-- ── Evidence Tags ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS evidence_tag (
    evidence_id INTEGER NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
    tag         TEXT    NOT NULL,
    PRIMARY KEY (evidence_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_evidence_tag_tag ON evidence_tag(tag);

-- ── Media ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS media (
    id             TEXT    PRIMARY KEY,  -- e.g. M-IMG-0001
    media_type     TEXT    NOT NULL,     -- image|video|audio|document
    description    TEXT    NOT NULL,
    file_path      TEXT    DEFAULT '',
    date_value     TEXT,
    date_precision TEXT,
    source_id      TEXT    REFERENCES source(id),
    review_status  TEXT    DEFAULT 'draft',
    notes          TEXT    DEFAULT ''
);

-- ── Media Segments ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS media_segment (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id    TEXT    NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    start_time  REAL,    -- seconds from start (for audio/video)
    end_time    REAL,
    label       TEXT    DEFAULT '',
    notes       TEXT    DEFAULT ''
);

-- ── Citation → Media ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS citation_media (
    citation_id INTEGER NOT NULL REFERENCES citation(id) ON DELETE CASCADE,
    media_id    TEXT    NOT NULL REFERENCES media(id)    ON DELETE CASCADE,
    PRIMARY KEY (citation_id, media_id)
);
