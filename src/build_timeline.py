#!/usr/bin/env python3
"""
build_timeline.py — Build a chronological timeline from the evidence database.

Four-agent pipeline:
  1. SQL pass     — high-certainty evidence from evidence.db
  1b. Year pass   — extract embedded dates from year-precision entries
  2. Profiles pass — pull key facts from profile markdown files (vault/profiles/)
  3. Curated pass — merge entries from curated-timeline.md (if present)
  4. Synthesis    — deduplicate, sort, and write timeline.md

Usage:
  python3 src/build_timeline.py              # full rebuild
  python3 src/build_timeline.py --full
  python3 src/build_timeline.py --incremental --since-hours 48
  python3 src/build_timeline.py --quiet
"""

import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from project_paths import VAULT, EVIDENCE_DB

# ── Configurable paths ────────────────────────────────────────────────────────
# Profiles directory: one markdown file per subject (optional)
PROFILES_DIR    = VAULT / "profiles"
# Curated timeline: hand-curated entries that override or supplement extracted facts
CURATED_TIMELINE = VAULT / "curated-timeline.md"
# Output file
OUTPUT_FILE     = VAULT / "timeline.md"

TODAY = date.today().isoformat()


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class TimelineEntry:
    date_sort:   str            # ISO date for sorting (YYYY-MM-DD or YYYY)
    date_display: str           # Human-readable date
    claim:       str
    certainty:   int   = 5
    status:      str   = 'alleged'
    source_ids:  list  = field(default_factory=list)
    people:      list  = field(default_factory=list)
    places:      list  = field(default_factory=list)
    tags:        list  = field(default_factory=list)
    legacy_id:   str   = ''
    origin:      str   = 'db'  # 'db' | 'profile' | 'curated'

    def confidence_badge(self) -> str:
        if self.certainty >= 8:
            return '●●●'  # high
        elif self.certainty >= 5:
            return '●●○'  # medium
        else:
            return '●○○'  # low

    def category_tag(self) -> str:
        tags_lower = ' '.join(self.tags).lower()
        people_lower = ' '.join(self.people).lower()
        if any(t in tags_lower for t in ['financial', 'investment', 'fund']):
            return '[FIN]'
        if any(t in tags_lower for t in ['travel', 'flight', 'location']):
            return '[TRV]'
        if any(t in tags_lower for t in ['legal', 'court', 'arrest', 'charge']):
            return '[LEG]'
        if any(t in tags_lower for t in ['relationship', 'social', 'meeting']):
            return '[SOC]'
        return '[GEN]'


# ── Utilities ─────────────────────────────────────────────────────────────────

def _parse_date(raw: str) -> Optional[str]:
    """Return ISO date string from various formats, or None."""
    if not raw:
        return None
    raw = raw.strip()
    # YYYY-MM-DD
    m = re.match(r'^(\d{4}-\d{2}-\d{2})$', raw)
    if m:
        return m.group(1)
    # YYYY-MM
    m = re.match(r'^(\d{4}-\d{2})$', raw)
    if m:
        return m.group(1) + '-01'
    # YYYY
    m = re.match(r'^(\d{4})$', raw)
    if m:
        return m.group(1) + '-01-01'
    # Written month: "March 1993" or "March 15, 1993"
    m = re.search(r'(\w+ \d{1,2},? \d{4})', raw)
    if m:
        try:
            dt = datetime.strptime(m.group(1).replace(',', ''), '%B %d %Y')
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            pass
    m = re.search(r'(\w+ \d{4})', raw)
    if m:
        try:
            dt = datetime.strptime(m.group(1), '%B %Y')
            return dt.strftime('%Y-%m-01')
        except ValueError:
            pass
    return None


def _format_date_display(iso: str) -> str:
    """Format an ISO date string for display."""
    if not iso:
        return 'undated'
    try:
        dt = datetime.strptime(iso[:10], '%Y-%m-%d')
        if iso.endswith('-01-01') and len(iso) == 10:
            return iso[:4]  # year only
        if iso.endswith('-01') and len(iso) == 7:
            return dt.strftime('%B %Y')
        return dt.strftime('%B %-d, %Y')
    except ValueError:
        return iso


def _words(text: str) -> set[str]:
    return set(re.findall(r'\b\w{4,}\b', text.lower()))


def _similarity(a: str, b: str) -> float:
    """Jaccard similarity between word sets of two strings."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _deduplicate(entries: list[TimelineEntry], threshold: float = 0.55) -> list[TimelineEntry]:
    """Remove near-duplicate entries, preferring higher certainty / earlier origin."""
    kept: list[TimelineEntry] = []
    for entry in entries:
        dupe = False
        for existing in kept:
            if existing.date_sort[:4] == entry.date_sort[:4]:  # same year
                sim = _similarity(existing.claim, entry.claim)
                if sim >= threshold:
                    # Keep higher certainty
                    if entry.certainty > existing.certainty:
                        kept.remove(existing)
                        kept.append(entry)
                    dupe = True
                    break
        if not dupe:
            kept.append(entry)
    return kept


# ── Agent 1: SQL pass ─────────────────────────────────────────────────────────

def agent1_sql_pass(db_path: Path, min_certainty: int = 5) -> list[TimelineEntry]:
    """Query evidence.db for dateable facts above the certainty threshold."""
    if not db_path.exists():
        print(f"  [SQL] Database not found: {db_path}", file=sys.stderr)
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT
            e.id, e.legacy_id, e.claim,
            e.date_value, e.date_precision,
            e.certainty, e.status
        FROM evidence e
        WHERE e.certainty >= ?
          AND e.date_value IS NOT NULL
          AND e.date_precision IN ('exact', 'month', 'year', 'approximate')
        ORDER BY e.date_value
    """, (min_certainty,)).fetchall()

    entries = []
    for row in rows:
        iso = _parse_date(row['date_value'])
        if not iso:
            continue

        # Load associated entities
        ents = conn.execute("""
            SELECT en.name, en.entity_type
            FROM evidence_entity ee JOIN entity en ON en.id = ee.entity_id
            WHERE ee.evidence_id = ?
        """, (row['id'],)).fetchall()

        tags = conn.execute(
            "SELECT tag FROM evidence_tag WHERE evidence_id = ?", (row['id'],)
        ).fetchall()

        sources = conn.execute(
            "SELECT source_id FROM citation WHERE evidence_id = ?", (row['id'],)
        ).fetchall()

        entries.append(TimelineEntry(
            date_sort    = iso,
            date_display = _format_date_display(iso),
            claim        = row['claim'],
            certainty    = row['certainty'] or 5,
            status       = row['status'] or 'alleged',
            source_ids   = [r['source_id'] for r in sources],
            people       = [e['name'] for e in ents if e['entity_type'] == 'person'],
            places       = [e['name'] for e in ents if e['entity_type'] == 'place'],
            tags         = [t['tag'] for t in tags],
            legacy_id    = row['legacy_id'] or str(row['id']),
            origin       = 'db',
        ))

    conn.close()
    print(f"  [SQL] {len(entries)} dateable entries (certainty >= {min_certainty})")
    return entries


# ── Agent 1b: Year-precision pass ─────────────────────────────────────────────

def agent1b_year_precision_pass(db_path: Path) -> list[TimelineEntry]:
    """
    For entries with year-only precision, try to extract a more precise date
    from embedded date strings within the claim text.
    """
    if not db_path.exists():
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT id, legacy_id, claim, date_value, certainty, status
        FROM evidence
        WHERE date_precision = 'year'
          AND certainty >= 5
          AND date_value IS NOT NULL
    """).fetchall()

    entries = []
    date_patterns = [
        r'\b(January|February|March|April|May|June|July|August|September|October|November|December)'
        r'\s+(\d{1,2}),?\s+(\d{4})\b',
        r'\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)'
        r'\s+(\d{4})\b',
    ]

    for row in rows:
        claim = row['claim']
        refined_iso = None

        for pat in date_patterns:
            m = re.search(pat, claim)
            if m:
                refined_iso = _parse_date(m.group(0))
                if refined_iso:
                    break

        if not refined_iso:
            continue

        entries.append(TimelineEntry(
            date_sort    = refined_iso,
            date_display = _format_date_display(refined_iso),
            claim        = claim,
            certainty    = row['certainty'] or 5,
            status       = row['status'] or 'alleged',
            legacy_id    = row['legacy_id'] or str(row['id']),
            origin       = 'db',
        ))

    conn.close()
    print(f"  [Year] {len(entries)} entries with refined dates")
    return entries


# ── Agent 2: Profiles pass ────────────────────────────────────────────────────

def agent2_profiles_pass(profiles_dir: Path) -> list[TimelineEntry]:
    """
    Scan profiles/*.md for timeline-relevant facts.
    Looks for date patterns + sentence pairs in profile files.
    """
    if not profiles_dir.exists():
        print(f"  [Profiles] Directory not found: {profiles_dir} — skipping")
        return []

    entries = []
    date_re = re.compile(
        r'\b(\d{4}-\d{2}-\d{2}|\d{4}-\d{2}|\d{4}|'
        r'(?:January|February|March|April|May|June|July|August|September|October|November|December)'
        r'\s+\d{1,2},?\s+\d{4})\b'
    )

    for md_file in sorted(profiles_dir.glob('*.md')):
        subject = md_file.stem.replace('-', ' ').title()
        text    = md_file.read_text(encoding='utf-8', errors='replace')

        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith('#') or len(line) < 20:
                continue

            m = date_re.search(line)
            if not m:
                continue

            iso = _parse_date(m.group(0))
            if not iso:
                continue

            # Strip leading bullets / dashes
            claim = re.sub(r'^[-*•]\s*', '', line)
            claim = re.sub(r'\[.*?\]\(.*?\)', '', claim).strip()  # remove markdown links

            if len(claim) < 15:
                continue

            entries.append(TimelineEntry(
                date_sort    = iso,
                date_display = _format_date_display(iso),
                claim        = claim,
                certainty    = 6,
                people       = [subject],
                origin       = 'profile',
            ))

    print(f"  [Profiles] {len(entries)} entries from {profiles_dir.name}/")
    return entries


# ── Agent 3: Curated pass ─────────────────────────────────────────────────────

def agent3_curated_pass(curated_file: Path) -> list[TimelineEntry]:
    """
    Read curated-timeline.md — a hand-maintained file of verified dates.
    Expected format per entry:
      ## YYYY-MM-DD or YYYY
      Claim text. (certainty: N)
    """
    if not curated_file.exists():
        print(f"  [Curated] Not found: {curated_file.name} — skipping")
        return []

    entries = []
    text = curated_file.read_text(encoding='utf-8', errors='replace')

    current_date = None
    current_claim_lines = []

    def flush():
        if current_date and current_claim_lines:
            claim = ' '.join(current_claim_lines).strip()
            iso   = _parse_date(current_date)
            if iso and len(claim) >= 15:
                cert_m = re.search(r'\(certainty:\s*(\d+)\)', claim)
                cert   = int(cert_m.group(1)) if cert_m else 8
                claim  = re.sub(r'\(certainty:\s*\d+\)', '', claim).strip()
                entries.append(TimelineEntry(
                    date_sort    = iso,
                    date_display = _format_date_display(iso),
                    claim        = claim,
                    certainty    = cert,
                    origin       = 'curated',
                ))

    for line in text.splitlines():
        h_match = re.match(r'^#{1,3}\s+(.+)$', line)
        if h_match:
            flush()
            current_date        = h_match.group(1).strip()
            current_claim_lines = []
        elif line.strip() and current_date:
            cleaned = re.sub(r'^[-*•]\s*', '', line.strip())
            if cleaned:
                current_claim_lines.append(cleaned)

    flush()
    print(f"  [Curated] {len(entries)} entries from {curated_file.name}")
    return entries


# ── Agent 4: Synthesis ────────────────────────────────────────────────────────

def agent4_synthesis(
    all_entries: list[TimelineEntry],
    output_file: Path,
    quiet: bool = False,
) -> None:
    """Deduplicate, sort, and write the timeline markdown file."""
    deduped = _deduplicate(all_entries)
    deduped.sort(key=lambda e: e.date_sort)

    if not quiet:
        print(f"  [Synthesis] {len(all_entries)} → {len(deduped)} after dedup")

    # Group by decade then year
    by_decade: dict[str, list[TimelineEntry]] = {}
    for e in deduped:
        year = int(e.date_sort[:4]) if e.date_sort[:4].isdigit() else 0
        decade = f"{(year // 10) * 10}s"
        by_decade.setdefault(decade, []).append(e)

    lines = [
        f"# Timeline",
        f"",
        f"*Generated {TODAY} — {len(deduped)} events*",
        f"*Sources: database, profiles, curated entries*",
        f"",
    ]

    for decade in sorted(by_decade.keys()):
        entries = by_decade[decade]
        lines.append(f"## {decade}")
        lines.append("")

        for e in entries:
            badge = e.confidence_badge()
            cat   = e.category_tag()
            claim = e.claim.rstrip('.')
            line  = f"- **{e.date_display}** {cat} {badge} — {claim}"
            if e.people:
                line += f" *(people: {', '.join(e.people[:3])})*"
            lines.append(line)
        lines.append("")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text('\n'.join(lines), encoding='utf-8')
    print(f"  [Synthesis] Written to {output_file}")


# ── Entry point ───────────────────────────────────────────────────────────────

def build_timeline(
    db_path: Path          = EVIDENCE_DB,
    profiles_dir: Path     = PROFILES_DIR,
    curated_file: Path     = CURATED_TIMELINE,
    output_file: Path      = OUTPUT_FILE,
    min_certainty: int     = 5,
    since_hours: int       = 0,
    quiet: bool            = False,
) -> None:
    if not quiet:
        print("Building timeline…")

    all_entries: list[TimelineEntry] = []

    print("\nAgent 1: SQL pass")
    all_entries += agent1_sql_pass(db_path, min_certainty)

    print("\nAgent 1b: Year-precision pass")
    all_entries += agent1b_year_precision_pass(db_path)

    print("\nAgent 2: Profiles pass")
    all_entries += agent2_profiles_pass(profiles_dir)

    print("\nAgent 3: Curated pass")
    all_entries += agent3_curated_pass(curated_file)

    print("\nAgent 4: Synthesis")
    agent4_synthesis(all_entries, output_file, quiet=quiet)


def main():
    import argparse
    p = argparse.ArgumentParser(description='Build chronological timeline')
    p.add_argument('--full',         action='store_true', help='Full rebuild (default)')
    p.add_argument('--incremental',  action='store_true', help='Incremental rebuild')
    p.add_argument('--since-hours',  type=int, default=0,
                   help='Hours lookback for incremental mode')
    p.add_argument('--quiet',        action='store_true', help='Suppress progress output')
    p.add_argument('--min-certainty', type=int, default=5, help='Minimum certainty threshold')
    p.add_argument('--db',           type=Path, default=EVIDENCE_DB)
    p.add_argument('--output',       type=Path, default=OUTPUT_FILE)
    args = p.parse_args()

    build_timeline(
        db_path       = args.db,
        output_file   = args.output,
        min_certainty = args.min_certainty,
        since_hours   = args.since_hours,
        quiet         = args.quiet,
    )


if __name__ == '__main__':
    main()
