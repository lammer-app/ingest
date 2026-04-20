#!/usr/bin/env python3
"""
search_facts.py — Query the evidence database (SQLite) or facts.jsonl directly.

Supports filtering by entity, tag, source, certainty, status, year, and date range.
Optionally runs AI analysis via Claude on a matched fact set.

Usage:
  python3 src/search_facts.py "keyword query"
  python3 src/search_facts.py --person "Name" --year 2005
  python3 src/search_facts.py --tag "travel" --certainty 7 --brief
  python3 src/search_facts.py --themes                   # list themes
  python3 src/search_facts.py --theme financier          # filter by theme
  python3 src/search_facts.py --ai "query" --ai-limit 30
"""

import json
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from project_paths import EVIDENCE_DB, FACTS_FILE

# ── Interpretive themes ──────────────────────────────────────────────────────
# Define investigative lenses relevant to your project. Each theme maps a slug
# to a set of comma-separated keywords. Used for scoring and --theme filtering.
#
# Example for an organized-crime investigation:
#   THEMES = {
#       "financier":  "accounts, transfers, investments, funds, fees, clients",
#       "network":    "associates, connections, meetings, introductions, allies",
#       "travel":     "flights, locations, hotels, addresses, properties",
#   }
#
THEMES: dict[str, str] = {}


# ── Claude binary location ────────────────────────────────────────────────────

def _find_claude() -> Path | None:
    """Locate the claude CLI binary."""
    found = shutil.which('claude')
    if found:
        return Path(found)
    for candidate in [
        Path.home() / '.nvm' / 'versions' / 'node' / 'v22.17.0' / 'bin' / 'claude',
        Path.home() / '.nvm' / 'versions' / 'node' / 'v22.22.0' / 'bin' / 'claude',
        Path('/usr/local/bin/claude'),
    ]:
        if candidate.exists():
            return candidate
    return None

CLAUDE_BIN = _find_claude()


# ── DB loader ────────────────────────────────────────────────────────────────

def load_facts_from_db(db_path: Path = EVIDENCE_DB) -> list[dict]:
    """Load evidence rows from SQLite as fact-compatible dicts."""
    if not db_path.exists():
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT
            e.id          AS db_id,
            e.legacy_id   AS id,
            e.claim,
            e.date_value  AS date_val,
            e.date_precision,
            e.certainty,
            e.status,
            e.review_status,
            e.notes,
            e.created,
            e.modified
        FROM evidence e
        ORDER BY e.date_value, e.id
    """).fetchall()

    facts = []
    for row in rows:
        d = dict(row)
        fid = d.get('id') or f"DB-{d['db_id']}"
        d['id'] = fid

        # Rebuild date object
        d['date'] = {
            'value':     d.pop('date_val', ''),
            'precision': d.pop('date_precision', 'year'),
        }

        # Load sources from citations table
        cites = conn.execute("""
            SELECT c.source_id, c.quote, c.page
            FROM citation c WHERE c.evidence_id = ?
        """, (d['db_id'],)).fetchall()
        d['sources'] = [dict(c) for c in cites]

        # Load people / places / orgs from entity table
        ents = conn.execute("""
            SELECT en.name, en.entity_type
            FROM evidence_entity ee
            JOIN entity en ON en.id = ee.entity_id
            WHERE ee.evidence_id = ?
        """, (d['db_id'],)).fetchall()
        d['people']        = [e['name'] for e in ents if e['entity_type'] == 'person']
        d['places']        = [e['name'] for e in ents if e['entity_type'] == 'place']
        d['organizations'] = [e['name'] for e in ents if e['entity_type'] == 'org']

        # Load tags
        tags = conn.execute("""
            SELECT tag FROM evidence_tag WHERE evidence_id = ?
        """, (d['db_id'],)).fetchall()
        d['tags'] = [t['tag'] for t in tags]

        # Load threads
        threads = conn.execute("""
            SELECT th.slug FROM evidence_thread et
            JOIN thread th ON th.id = et.thread_id
            WHERE et.evidence_id = ?
        """, (d['db_id'],)).fetchall()
        d['themes'] = [t['slug'] for t in threads]

        facts.append(d)

    conn.close()
    return facts


def load_facts(db_path: Path = EVIDENCE_DB, jsonl_path: Path = FACTS_FILE) -> list[dict]:
    """Load facts from SQLite if available, else fall back to JSONL."""
    if db_path.exists():
        facts = load_facts_from_db(db_path)
        if facts:
            return facts

    if jsonl_path.exists():
        facts = []
        for line in jsonl_path.read_text().splitlines():
            if line.strip():
                try:
                    facts.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return facts

    return []


# ── Scoring ───────────────────────────────────────────────────────────────────

def score_fact(fact: dict, query: str) -> float:
    """Score a fact against a text query. Higher = more relevant."""
    tokens = set(re.findall(r'\w+', query.lower()))
    if not tokens:
        return 0.0

    claim = fact.get('claim', '').lower()
    people = ' '.join(fact.get('people', [])).lower()
    places = ' '.join(fact.get('places', [])).lower()
    tags   = ' '.join(fact.get('tags',   [])).lower()

    score = 0.0
    for token in tokens:
        if token in claim:
            score += 2.0
        if token in people or token in places:
            score += 1.5
        if token in tags:
            score += 1.0

    # Certainty bonus
    cert = fact.get('certainty', 5)
    score += cert * 0.1

    return score


def score_theme(fact: dict, theme_slug: str) -> float:
    """Score a fact against a named theme's keywords."""
    keywords_str = THEMES.get(theme_slug, '')
    if not keywords_str:
        return 0.0
    keywords = [k.strip().lower() for k in keywords_str.split(',') if k.strip()]
    claim = fact.get('claim', '').lower()
    return sum(2.0 for kw in keywords if kw in claim)


# ── AI analysis ──────────────────────────────────────────────────────────────

def ai_analyze(facts: list[dict], query: str, limit: int = 50) -> str:
    """Run Claude on a fact set and return its analysis."""
    if not CLAUDE_BIN:
        return "Claude binary not found. Install claude CLI to use --ai."

    subset = facts[:limit]
    blob = '\n'.join(
        f"[{f.get('id','')}] {f.get('claim','')} "
        f"(date: {f.get('date',{}).get('value','?')}, "
        f"certainty: {f.get('certainty','?')})"
        for f in subset
    )

    prompt = (
        f"You are an investigative research assistant. Analyze the following "
        f"{len(subset)} facts in response to this question: {query}\n\n"
        f"Identify patterns, contradictions, and key findings. Be concise.\n\n"
        f"FACTS:\n{blob}"
    )

    env = {k: v for k, v in __import__('os').environ.items() if k != 'CLAUDECODE'}
    try:
        result = subprocess.run(
            [str(CLAUDE_BIN), '-p', prompt],
            capture_output=True, text=True, timeout=120, env=env,
        )
        return result.stdout.strip() or result.stderr.strip() or '(no output)'
    except subprocess.TimeoutExpired:
        return 'AI analysis timed out (120s)'
    except Exception as e:
        return f'AI analysis failed: {e}'


# ── Formatting ────────────────────────────────────────────────────────────────

def _date_str(fact: dict) -> str:
    d = fact.get('date', {})
    if isinstance(d, dict):
        return d.get('value', 'undated')
    return str(d) if d else 'undated'


def format_brief(facts: list[dict]) -> str:
    lines = []
    for f in facts:
        cert  = f.get('certainty', '?')
        date  = _date_str(f)
        claim = f.get('claim', '')[:120]
        lines.append(f"[{f.get('id','')}] {date} (c={cert}) {claim}")
    return '\n'.join(lines)


def format_full(facts: list[dict]) -> str:
    blocks = []
    for f in facts:
        lines = [
            f"ID:        {f.get('id','')}",
            f"Date:      {_date_str(f)}",
            f"Certainty: {f.get('certainty','?')} / 10",
            f"Status:    {f.get('status','?')}",
            f"Claim:     {f.get('claim','')}",
        ]
        if f.get('people'):
            lines.append(f"People:    {', '.join(f['people'])}")
        if f.get('places'):
            lines.append(f"Places:    {', '.join(f['places'])}")
        if f.get('tags'):
            lines.append(f"Tags:      {', '.join(f['tags'])}")
        if f.get('sources'):
            for s in f['sources']:
                sid  = s.get('source_id', '')
                page = s.get('page', '')
                lines.append(f"Source:    {sid}" + (f", p.{page}" if page else ''))
        blocks.append('\n'.join(lines))
    return '\n\n---\n\n'.join(blocks)


def format_cite(facts: list[dict]) -> str:
    lines = []
    for f in facts:
        sids = ', '.join(s.get('source_id', '') for s in f.get('sources', []))
        lines.append(f"[{f.get('id','')}] {f.get('claim','')[:100]} ({sids})")
    return '\n'.join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    import argparse

    p = argparse.ArgumentParser(description='Search the evidence database')
    p.add_argument('query', nargs='?', default='', help='Free-text search query')

    # Entity / tag filters
    p.add_argument('--person',    help='Filter by person name (substring)')
    p.add_argument('--place',     help='Filter by place name (substring)')
    p.add_argument('--tag',       help='Filter by tag')
    p.add_argument('--source',    help='Filter by source_id')
    p.add_argument('--status',    help='Filter by status (alleged/confirmed/disputed/disproven)')
    p.add_argument('--year',      type=int, help='Filter by year')
    p.add_argument('--certainty', type=int, help='Minimum certainty (0–10)')

    # Theme filters
    p.add_argument('--themes', action='store_true', help='List available themes')
    p.add_argument('--theme',  help='Filter and score by theme slug')

    # AI
    p.add_argument('--ai',       action='store_true', help='Run AI analysis on results')
    p.add_argument('--ai-limit', type=int, default=50, help='Max facts for AI analysis')

    # Output
    p.add_argument('--brief', action='store_true', help='One-line output per fact')
    p.add_argument('--full',  action='store_true', help='Full detail output')
    p.add_argument('--cite',  action='store_true', help='Citation-style output')
    p.add_argument('--json',  action='store_true', help='Raw JSON output')
    p.add_argument('--count', action='store_true', help='Print count only')
    p.add_argument('--limit', type=int, default=100, help='Max results')
    p.add_argument('--db',    type=Path, default=EVIDENCE_DB, help='SQLite database path')

    args = p.parse_args()

    # List themes
    if args.themes:
        if not THEMES:
            print("No themes defined. Add entries to THEMES dict in search_facts.py.")
        else:
            print("Available themes:")
            for slug, kw in THEMES.items():
                print(f"  {slug:<20} {kw[:60]}")
        return

    # Load
    facts = load_facts(db_path=args.db)
    if not facts:
        print(f"No facts found. Run extract_facts.py to populate facts.jsonl.", file=sys.stderr)
        sys.exit(1)

    # Apply filters
    results = facts

    if args.query:
        scored = [(score_fact(f, args.query), f) for f in results]
        scored = [(s, f) for s, f in scored if s > 0]
        scored.sort(key=lambda x: x[0], reverse=True)
        results = [f for _, f in scored]

    if args.theme:
        if args.theme not in THEMES:
            print(f"Unknown theme '{args.theme}'. Use --themes to list available.", file=sys.stderr)
            sys.exit(1)
        scored = [(score_theme(f, args.theme), f) for f in results]
        scored = [(s, f) for s, f in scored if s > 0]
        scored.sort(key=lambda x: x[0], reverse=True)
        results = [f for _, f in scored]

    if args.person:
        q = args.person.lower()
        results = [f for f in results if any(q in p.lower() for p in f.get('people', []))]

    if args.place:
        q = args.place.lower()
        results = [f for f in results if any(q in pl.lower() for pl in f.get('places', []))]

    if args.tag:
        q = args.tag.lower()
        results = [f for f in results if any(q in t.lower() for t in f.get('tags', []))]

    if args.source:
        q = args.source.lower()
        results = [
            f for f in results
            if any(q in s.get('source_id', '').lower() for s in f.get('sources', []))
        ]

    if args.status:
        results = [f for f in results if f.get('status') == args.status]

    if args.year:
        results = [
            f for f in results
            if str(args.year) in str(f.get('date', {}).get('value', ''))
        ]

    if args.certainty is not None:
        results = [f for f in results if (f.get('certainty') or 0) >= args.certainty]

    # Cap
    results = results[:args.limit]

    # Output
    if args.count:
        print(len(results))
        return

    if args.json:
        for f in results:
            print(json.dumps({k: v for k, v in f.items() if not k.startswith('_')},
                             ensure_ascii=False))
        return

    if args.full:
        print(format_full(results))
    elif args.cite:
        print(format_cite(results))
    else:
        print(format_brief(results))

    print(f"\n({len(results)} results)")

    # AI analysis
    if args.ai:
        query_text = args.query or args.theme or 'general analysis'
        print(f"\n── AI Analysis ({'claude' if CLAUDE_BIN else 'unavailable'}) ──")
        print(ai_analyze(results, query_text, limit=args.ai_limit))


if __name__ == '__main__':
    main()
