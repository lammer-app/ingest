#!/usr/bin/env python3
"""
migrate_to_sqlite.py — Migrate JSONL fact store to SQLite evidence database.

Reads facts.jsonl, sources.jsonl, and media.jsonl and populates the relational
evidence.db defined in db/schema.sql.

Steps:
  1. Open (or create) evidence.db from schema.sql
  2. Insert sources
  3. Insert media records
  4. Collect and insert entities (people, places, organizations)
  5. Insert evidence rows + citations
  6. Link evidence → entities
  7. Apply tags
  8. Seed threads from THEMES dict (if any defined in config)
  9. Backfill evidence → threads

Usage:
  python3 src/migrate_to_sqlite.py
  python3 src/migrate_to_sqlite.py --facts data/facts/facts.jsonl
  python3 src/migrate_to_sqlite.py --force     # drop and recreate tables
  python3 src/migrate_to_sqlite.py --dry-run   # validate only, no writes
"""

import json
import re
import sqlite3
import sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent))
from project_paths import (
    EVIDENCE_DB, FACTS_FILE, MEDIA_FILE, SCHEMA_SQL,
    SOURCES_FILE, ensure_dirs,
)

TODAY = date.today().isoformat()

# ── Theme seeds (optional) ──────────────────────────────────────────────────
# Define interpretive lenses for your project. Each theme maps to a set of
# keywords used to bucket evidence into narrative threads. Leave empty to
# skip thread seeding.
#
# Example:
#   THEMES = {
#       "financier": "investments, clients, funds, transactions, fees",
#       "network":   "associates, connections, introductions, meetings",
#       "travel":    "flights, locations, addresses, properties, trips",
#   }
#
THEMES: dict[str, str] = {}


# ── Helpers ──────────────────────────────────────────────────────────────────

def sanitize_synthetic_id(raw: str) -> str:
    """Normalize synthetic IDs: strip whitespace, truncate to 200 chars."""
    return (raw or '').strip()[:200]


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"  Warning: skipping malformed line in {path.name}: {e}",
                      file=sys.stderr)
    return rows


def _open_db(db_path: Path, schema_path: Path, force: bool = False) -> sqlite3.Connection:
    """Open (or create) the SQLite database and apply schema."""
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if force and db_path.exists():
        db_path.unlink()
        print(f"  Dropped {db_path.name}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    if schema_path.exists():
        conn.executescript(schema_path.read_text())
        conn.commit()
        print(f"  Applied schema from {schema_path.name}")
    else:
        print(f"  Warning: schema not found at {schema_path}", file=sys.stderr)

    return conn


# ── Step 1: Sources ──────────────────────────────────────────────────────────

def insert_sources(conn: sqlite3.Connection, sources: list[dict]) -> int:
    inserted = 0
    for s in sources:
        sid = s.get('id', '')
        if not sid:
            continue
        conn.execute("""
            INSERT OR IGNORE INTO source
              (id, title, author, year, source_type, certainty_base,
               is_memoir, extraction_status, facts_extracted, vault_path, notes, updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            sid,
            s.get('title', ''),
            s.get('author', 'Unknown'),
            s.get('year'),
            s.get('type', 'book'),
            s.get('certainty_base', 7),
            1 if s.get('is_memoir') else 0,
            s.get('extraction_status', 'pending'),
            s.get('facts_extracted', 0),
            s.get('vault_path', ''),
            s.get('notes', ''),
            s.get('updated', TODAY),
        ))
        if conn.execute("SELECT changes()").fetchone()[0]:
            inserted += 1
    conn.commit()
    print(f"  Sources: {inserted} inserted ({len(sources)} total)")
    return inserted


# ── Step 2: Media ────────────────────────────────────────────────────────────

def insert_media(conn: sqlite3.Connection, media: list[dict]) -> int:
    inserted = 0
    for m in media:
        mid = m.get('id', '')
        if not mid:
            continue
        conn.execute("""
            INSERT OR IGNORE INTO media
              (id, media_type, description, file_path, date_value, date_precision,
               source_id, review_status, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            mid,
            m.get('type', 'image'),
            m.get('description', ''),
            m.get('file_path', ''),
            m.get('date', {}).get('value') if isinstance(m.get('date'), dict) else None,
            m.get('date', {}).get('precision') if isinstance(m.get('date'), dict) else None,
            m.get('source_id'),
            m.get('review_status', 'draft'),
            m.get('notes', ''),
        ))
        if conn.execute("SELECT changes()").fetchone()[0]:
            inserted += 1
    conn.commit()
    print(f"  Media: {inserted} inserted ({len(media)} total)")
    return inserted


# ── Step 3: Entities ─────────────────────────────────────────────────────────

def collect_entities(facts: list[dict]) -> dict[str, dict]:
    """Collect unique entity names from fact entity arrays."""
    entities: dict[str, dict] = {}  # normalized_name → {name, type}

    for fact in facts:
        for field, etype in [('people', 'person'), ('places', 'place'), ('organizations', 'org')]:
            for name in fact.get(field, []):
                key = name.strip().lower()
                if key and key not in entities:
                    entities[key] = {'name': name.strip(), 'type': etype}

    return entities


def insert_entities(conn: sqlite3.Connection, entities: dict[str, dict]) -> dict[str, int]:
    """Insert entities and return {normalized_name: entity_id}."""
    name_to_id: dict[str, int] = {}

    for key, meta in entities.items():
        cur = conn.execute("""
            INSERT OR IGNORE INTO entity (name, entity_type) VALUES (?, ?)
        """, (meta['name'], meta['type']))
        row = conn.execute(
            "SELECT id FROM entity WHERE name = ?", (meta['name'],)
        ).fetchone()
        if row:
            name_to_id[key] = row['id']

    conn.commit()
    print(f"  Entities: {len(name_to_id)} upserted")
    return name_to_id


# ── Step 4: Evidence + Citations ─────────────────────────────────────────────

def resolve_citations(fact: dict, sources: list[dict]) -> list[tuple]:
    """
    Resolve source citations for a fact. Returns list of (source_id, quote, page) tuples.

    Fallback chain:
      A) source_id present in fact.sources[] → use directly
      B) ref string matches a source's ref field → resolve
      C) insert as SRC-UNKNOWN
    """
    source_refs: dict[str, str] = {}  # ref string → source_id
    for s in sources:
        if s.get('ref'):
            source_refs[s['ref'].lower().strip()] = s['id']

    citations = []
    for src_entry in fact.get('sources', []):
        sid = src_entry.get('source_id', '').strip()
        quote = src_entry.get('quote', '') or ''
        page  = src_entry.get('page', '') or ''
        ref   = src_entry.get('ref', '') or ''

        if sid:
            citations.append((sid, quote, page))
        elif ref:
            resolved = source_refs.get(ref.lower().strip())
            citations.append((resolved or f'SRC-UNKNOWN-{ref[:20]}', quote, page))
        else:
            citations.append(('SRC-UNKNOWN', quote, page))

    return citations or [('SRC-UNKNOWN', '', '')]


def insert_evidence_and_citations(
    conn: sqlite3.Connection,
    facts: list[dict],
    sources: list[dict],
) -> dict[str, int]:
    """Insert evidence rows and their citations. Returns {fact_id: evidence_db_id}."""
    fact_to_eid: dict[str, int] = {}

    for fact in facts:
        fid = fact.get('id', '')
        if not fid:
            continue

        date_val = None
        date_prec = None
        d = fact.get('date', {})
        if isinstance(d, dict):
            date_val  = d.get('value')
            date_prec = d.get('precision', 'year')

        conn.execute("""
            INSERT OR IGNORE INTO evidence
              (legacy_id, claim, date_value, date_precision,
               certainty, status, review_status, notes,
               created, modified)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            fid,
            fact.get('claim', ''),
            date_val,
            date_prec,
            fact.get('certainty', 5),
            fact.get('status', 'alleged'),
            fact.get('review_status', 'draft'),
            fact.get('notes', ''),
            fact.get('created', TODAY),
            fact.get('modified', TODAY),
        ))

        row = conn.execute(
            "SELECT id FROM evidence WHERE legacy_id = ?", (fid,)
        ).fetchone()
        if not row:
            continue

        eid = row['id']
        fact_to_eid[fid] = eid

        # Insert citations
        for source_id, quote, page in resolve_citations(fact, sources):
            conn.execute("""
                INSERT OR IGNORE INTO citation (evidence_id, source_id, quote, page)
                VALUES (?, ?, ?, ?)
            """, (eid, source_id, quote, page))

    conn.commit()
    print(f"  Evidence: {len(fact_to_eid)} rows inserted")
    return fact_to_eid


# ── Step 5: Evidence → Entities ──────────────────────────────────────────────

def insert_evidence_entities(
    conn: sqlite3.Connection,
    facts: list[dict],
    fact_to_eid: dict[str, int],
    name_to_entity_id: dict[str, int],
) -> None:
    count = 0
    for fact in facts:
        fid = fact.get('id', '')
        eid = fact_to_eid.get(fid)
        if not eid:
            continue

        for field in ['people', 'places', 'organizations']:
            for name in fact.get(field, []):
                entity_id = name_to_entity_id.get(name.strip().lower())
                if entity_id:
                    conn.execute("""
                        INSERT OR IGNORE INTO evidence_entity (evidence_id, entity_id)
                        VALUES (?, ?)
                    """, (eid, entity_id))
                    count += 1

    conn.commit()
    print(f"  Evidence-entity links: {count} inserted")


# ── Step 6: Evidence Tags ────────────────────────────────────────────────────

def insert_evidence_tags(
    conn: sqlite3.Connection,
    facts: list[dict],
    fact_to_eid: dict[str, int],
) -> None:
    count = 0
    for fact in facts:
        fid = fact.get('id', '')
        eid = fact_to_eid.get(fid)
        if not eid:
            continue

        for tag in fact.get('tags', []):
            tag = tag.strip()
            if not tag:
                continue
            conn.execute("""
                INSERT OR IGNORE INTO evidence_tag (evidence_id, tag)
                VALUES (?, ?)
            """, (eid, tag))
            count += 1

    conn.commit()
    print(f"  Evidence tags: {count} inserted")


# ── Step 7: Seed Threads ─────────────────────────────────────────────────────

def seed_threads(conn: sqlite3.Connection) -> dict[str, int]:
    """Seed thread rows from THEMES dict. Returns {theme_slug: thread_id}."""
    theme_to_tid: dict[str, int] = {}

    if not THEMES:
        print("  Threads: no themes defined (THEMES is empty) — skipping")
        return theme_to_tid

    for slug, keywords in THEMES.items():
        conn.execute("""
            INSERT OR IGNORE INTO thread (slug, title, keywords)
            VALUES (?, ?, ?)
        """, (slug, slug.replace('-', ' ').title(), keywords))
        row = conn.execute(
            "SELECT id FROM thread WHERE slug = ?", (slug,)
        ).fetchone()
        if row:
            theme_to_tid[slug] = row['id']

    conn.commit()
    print(f"  Threads: {len(theme_to_tid)} seeded from THEMES")
    return theme_to_tid


# ── Step 8: Backfill Evidence → Threads ──────────────────────────────────────

def backfill_evidence_threads(
    conn: sqlite3.Connection,
    facts: list[dict],
    fact_to_eid: dict[str, int],
    theme_to_tid: dict[str, int],
) -> None:
    """Link evidence to threads based on keyword matching."""
    if not theme_to_tid:
        return

    # Build keyword → thread_id lookup
    kw_map: list[tuple[list[str], int]] = []
    for slug, tid in theme_to_tid.items():
        keywords = THEMES.get(slug, '')
        words = [w.strip().lower() for w in keywords.split(',') if w.strip()]
        kw_map.append((words, tid))

    count = 0
    for fact in facts:
        fid = fact.get('id', '')
        eid = fact_to_eid.get(fid)
        if not eid:
            continue

        claim = fact.get('claim', '').lower()
        for words, tid in kw_map:
            if any(w in claim for w in words):
                conn.execute("""
                    INSERT OR IGNORE INTO evidence_thread (evidence_id, thread_id)
                    VALUES (?, ?)
                """, (eid, tid))
                count += 1

    conn.commit()
    print(f"  Evidence-thread links: {count} inserted")


# ── Main ─────────────────────────────────────────────────────────────────────

def migrate(
    facts_file: Path = FACTS_FILE,
    sources_file: Path = SOURCES_FILE,
    media_file: Path = MEDIA_FILE,
    db_path: Path = EVIDENCE_DB,
    schema_path: Path = SCHEMA_SQL,
    force: bool = False,
    dry_run: bool = False,
) -> None:
    print(f"Loading JSONL files…")
    facts   = load_jsonl(facts_file)
    sources = load_jsonl(sources_file)
    media   = load_jsonl(media_file)
    print(f"  {len(facts)} facts, {len(sources)} sources, {len(media)} media")

    if dry_run:
        print("Dry run — no database writes.")
        return

    ensure_dirs()
    print(f"\nOpening database: {db_path}")
    conn = _open_db(db_path, schema_path, force=force)

    print("\nStep 1: Sources")
    insert_sources(conn, sources)

    print("\nStep 2: Media")
    insert_media(conn, media)

    print("\nStep 3: Entities")
    entities = collect_entities(facts)
    name_to_entity_id = insert_entities(conn, entities)

    print("\nStep 4: Evidence + Citations")
    fact_to_eid = insert_evidence_and_citations(conn, facts, sources)

    print("\nStep 5: Evidence → Entities")
    insert_evidence_entities(conn, facts, fact_to_eid, name_to_entity_id)

    print("\nStep 6: Evidence Tags")
    insert_evidence_tags(conn, facts, fact_to_eid)

    print("\nStep 7: Threads")
    theme_to_tid = seed_threads(conn)

    print("\nStep 8: Backfill Evidence → Threads")
    backfill_evidence_threads(conn, facts, fact_to_eid, theme_to_tid)

    conn.close()
    print(f"\nDone. Database at: {db_path}")


def main():
    import argparse
    p = argparse.ArgumentParser(description='Migrate JSONL fact store to SQLite')
    p.add_argument('--facts',    type=Path, default=FACTS_FILE)
    p.add_argument('--sources',  type=Path, default=SOURCES_FILE)
    p.add_argument('--media',    type=Path, default=MEDIA_FILE)
    p.add_argument('--db',       type=Path, default=EVIDENCE_DB)
    p.add_argument('--schema',   type=Path, default=SCHEMA_SQL)
    p.add_argument('--force',    action='store_true', help='Drop and recreate database')
    p.add_argument('--dry-run',  action='store_true', help='Validate only, no writes')
    args = p.parse_args()

    migrate(
        facts_file=args.facts,
        sources_file=args.sources,
        media_file=args.media,
        db_path=args.db,
        schema_path=args.schema,
        force=args.force,
        dry_run=args.dry_run,
    )


if __name__ == '__main__':
    main()
