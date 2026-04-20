#!/usr/bin/env python3
"""
extract_facts.py — Extract factual claims from source documents into facts.jsonl.

Unlike chapter extraction (which parses inline annotations), this script
identifies factual claims within narrative prose. The source IS the document.

Approach:
  1. Split text into paragraphs, then sentences
  2. Score each sentence for factual content (dates, names, places, specifics)
  3. High-scoring sentences become candidate facts
  4. Deduplicate against existing facts in facts.jsonl
  5. Output new facts tagged with the source

Source metadata is loaded from config/sources/*.toml files.
Entity aliases (people, places) are loaded from config/entities.toml if present.

Usage:
  python3 src/extract_facts.py my-book.md
  python3 src/extract_facts.py my-book.md --dry-run
  python3 src/extract_facts.py --all         # Process all books in BOOKS_DIR
  python3 src/extract_facts.py --articles    # Process all articles in SOURCES_DIR
"""

import json
import re
import sys
import os
import argparse
from datetime import date
from pathlib import Path
from collections import defaultdict, Counter

# ── Path setup ────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from project_paths import (
    VAULT, BOOKS_DIR, SOURCES_DIR, FACTS_FILE, MEDIA_FILE,
    SOURCES_FILE, LOG_FILE, ensure_dirs
)

TODAY = date.today().isoformat()

# ── Config loading ────────────────────────────────────────────────────────

def _load_toml_simple(path: Path) -> dict:
    """
    Minimal TOML parser for flat [section] + key = value files.
    Uses tomllib (Python 3.11+ stdlib) when available, otherwise falls back
    to a regex-based parser that handles the subset used in source configs.
    """
    try:
        import tomllib
        return tomllib.loads(path.read_text())
    except ImportError:
        pass

    result = {}
    section = None
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r'^\[(\w[\w.]*)\]$', line)
        if m:
            section = m.group(1)
            result.setdefault(section, {})
            continue
        if "=" in line and section is not None:
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            # Unquote strings
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            elif val.startswith("'") and val.endswith("'"):
                val = val[1:-1]
            # Booleans
            elif val.lower() == "true":
                val = True
            elif val.lower() == "false":
                val = False
            else:
                # Try int/float
                try:
                    val = int(val)
                except ValueError:
                    try:
                        val = float(val)
                    except ValueError:
                        pass
            result[section][key] = val
    return result


def load_source_configs() -> dict:
    """
    Load all source metadata from config/sources/*.toml.
    Returns {slug: metadata_dict}.
    """
    config_dir = VAULT / "config" / "sources"
    sources = {}
    if not config_dir.exists():
        return sources
    for toml_file in config_dir.glob("*.toml"):
        try:
            data = _load_toml_simple(toml_file)
            src = data.get("source", {})
            slug = toml_file.stem  # filename without .toml
            sources[slug] = src
        except Exception as e:
            print(f"  Warning: could not parse {toml_file.name}: {e}", file=sys.stderr)
    return sources


def load_entity_aliases() -> tuple[dict, dict]:
    """
    Load people and place aliases from config/entities.toml if it exists.
    Returns (people_aliases, place_aliases) as {canonical_name: [alias, alias, ...]}.
    """
    entities_file = VAULT / "config" / "entities.toml"
    if not entities_file.exists():
        return {}, {}
    data = _load_toml_simple(entities_file)
    people = data.get("people", {})
    places = data.get("places", {})
    return people, places


# ── Scoring indicators ────────────────────────────────────────────────────

FACTUAL_INDICATORS = [
    # Dates / times
    r'\b(19|20)\d{2}\b',           # Years 1900-2099
    r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\b',
    r'\b\d{1,2}(st|nd|rd|th)?\s+(of\s+)?(January|February|March|April|May|June|July|August|September|October|November|December)\b',
    r'\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b',

    # Numbers and quantities
    r'\$[\d,]+(?:\.\d+)?(?:\s*(?:million|billion|thousand))?\b',
    r'\b\d+(?:,\d{3})*(?:\.\d+)?\s*(?:million|billion|thousand|percent|%)\b',
    r'\b\d{1,3}(?:,\d{3})+\b',     # Large numbers

    # Locations
    r'\b(?:New York|London|Paris|Washington|Miami|Palm Beach|Los Angeles|Manhattan|Brooklyn)\b',

    # Legal / institutional
    r'\b(?:arrested|charged|convicted|sentenced|pleaded|testified|deposed|filed|signed|agreed)\b',
    r'\b(?:FBI|CIA|DOJ|SEC|IRS|SDNY|DOD)\b',
    r'\b(?:court|judge|attorney|lawyer|prosecutor|defendant|plaintiff)\b',

    # Property / finance
    r'\b(?:purchased|acquired|sold|leased|owned|transferred|donated)\b',
    r'\b(?:agreement|contract|deed|trust|estate|will|settlement)\b',
]

FACTUAL_PATTERNS = [re.compile(p, re.IGNORECASE) for p in FACTUAL_INDICATORS]

SKIP_STARTERS = [
    r'^#+\s',         # Markdown headers
    r'^---',          # YAML fences / HR
    r'^\*\*\*',       # HR
    r'^\|',           # Tables
    r'^\[',           # Link/footnote lines
    r'^>\s',          # Blockquotes (usually quotes already extracted)
    r'^```',          # Code blocks
    r'^\s*$',         # Empty
]
SKIP_PATTERNS = [re.compile(p) for p in SKIP_STARTERS]


def score_sentence(sentence: str) -> int:
    """
    Score a sentence for factual content.
    Returns 0-N where higher means more likely to be a discrete fact.
    """
    score = 0
    for pattern in FACTUAL_PATTERNS:
        if pattern.search(sentence):
            score += 1
    # Bonus: multiple indicator types in one sentence
    if score >= 3:
        score += 1
    # Penalty: very short sentences (likely headings or fragments)
    if len(sentence.split()) < 8:
        score = max(0, score - 2)
    return score


def split_into_sentences(text: str) -> list[str]:
    """Split a paragraph into sentences (simple heuristic)."""
    # Split on sentence-ending punctuation followed by whitespace + capital
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z"])', text)
    return [s.strip() for s in sentences if s.strip()]


def is_skippable(line: str) -> bool:
    """Return True if this line should not be processed."""
    for p in SKIP_PATTERNS:
        if p.match(line):
            return True
    return False


# ── Deduplication ─────────────────────────────────────────────────────────

def load_existing_facts() -> tuple[list[dict], set[str]]:
    """Load existing facts and build a fingerprint set for dedup."""
    facts = []
    fingerprints = set()
    if not FACTS_FILE.exists():
        return facts, fingerprints
    for line in FACTS_FILE.read_text().strip().split('\n'):
        if not line.strip():
            continue
        try:
            f = json.loads(line)
            facts.append(f)
            fingerprints.add(_fingerprint(f.get('claim', '')))
        except json.JSONDecodeError:
            pass
    return facts, fingerprints


def _fingerprint(claim: str) -> str:
    """Normalize claim text for dedup comparison."""
    return re.sub(r'\s+', ' ', claim.lower().strip())[:200]


def next_fact_id(existing_facts: list[dict], date_prefix: str) -> str:
    """Generate the next sequential fact ID for this date prefix."""
    prefix = f"F-{date_prefix}-"
    existing_nums = []
    for f in existing_facts:
        fid = f.get('id', '')
        if fid.startswith(prefix):
            try:
                existing_nums.append(int(fid[len(prefix):]))
            except ValueError:
                pass
    next_num = max(existing_nums, default=0) + 1
    return f"{prefix}{next_num:04d}"


# ── Entity extraction (simple heuristic) ─────────────────────────────────

def extract_entities(sentence: str, people_aliases: dict, place_aliases: dict) -> tuple[list, list]:
    """
    Extract people and place mentions from a sentence using the alias tables.
    Returns (people_slugs, place_slugs).
    """
    people_found = []
    places_found = []
    sentence_lower = sentence.lower()

    for canonical, aliases in people_aliases.items():
        all_names = [canonical] + (aliases if isinstance(aliases, list) else [aliases])
        for name in all_names:
            if name.lower() in sentence_lower:
                slug = re.sub(r'[^a-z0-9]+', '-', canonical.lower()).strip('-')
                if slug not in people_found:
                    people_found.append(slug)
                break

    for canonical, aliases in place_aliases.items():
        all_names = [canonical] + (aliases if isinstance(aliases, list) else [aliases])
        for name in all_names:
            if name.lower() in sentence_lower:
                slug = re.sub(r'[^a-z0-9]+', '-', canonical.lower()).strip('-')
                if slug not in places_found:
                    places_found.append(slug)
                break

    return people_found, places_found


def extract_date_from_sentence(sentence: str) -> dict:
    """Try to extract a year or date from the sentence."""
    # Full date
    m = re.search(r'\b(19|20)(\d{2})\b', sentence)
    if m:
        return {'value': m.group(0), 'precision': 'year'}
    return {'value': 'XXXX', 'precision': 'undated'}


# ── Extraction core ───────────────────────────────────────────────────────

def extract_from_file(filepath: Path, source_meta: dict, dry_run: bool = False,
                      people_aliases: dict = None, place_aliases: dict = None,
                      score_threshold: int = None) -> list[dict]:
    """
    Extract facts from a markdown file. Returns list of new fact dicts.
    """
    if people_aliases is None:
        people_aliases = {}
    if place_aliases is None:
        place_aliases = {}

    text = filepath.read_text(encoding='utf-8', errors='replace')

    # Strip page markers from HTML-extracted EPUBs
    text = re.sub(r'--- Page \d+ ---\n', '', text)

    source_id    = source_meta.get('id', f'SRC-UNKNOWN-{filepath.stem}')
    ref          = source_meta.get('ref', filepath.stem)
    certainty    = source_meta.get('certainty_base', 7)
    threshold    = score_threshold or source_meta.get('score_threshold', 5)
    is_memoir    = source_meta.get('is_memoir', False)

    existing_facts, fingerprints = load_existing_facts()
    new_facts = []

    paragraphs = re.split(r'\n{2,}', text)
    sentences_scanned = 0
    skipped_dupes = 0

    # Use year from source metadata if available, else current year
    source_year = str(source_meta.get('year', TODAY[:4]))
    date_prefix = source_year

    for para in paragraphs:
        para = para.strip()
        if not para or is_skippable(para):
            continue

        for sentence in split_into_sentences(para):
            sentences_scanned += 1
            if len(sentence.split()) < 8:
                continue

            score = score_sentence(sentence)
            if score < threshold:
                continue

            fp = _fingerprint(sentence)
            if fp in fingerprints:
                skipped_dupes += 1
                continue

            date_obj = extract_date_from_sentence(sentence)
            year_val = date_obj['value'][:4] if date_obj['value'] != 'XXXX' else source_year
            fact_id  = next_fact_id(existing_facts + new_facts, year_val)

            people, places = extract_entities(sentence, people_aliases, place_aliases)

            fact = {
                'id': fact_id,
                'claim': sentence,
                'date': date_obj,
                'certainty': certainty,
                'status': 'alleged' if is_memoir else 'confirmed',
                'review_status': 'draft',
                'sources': [{
                    'source_id': source_id,
                    'ref': ref,
                    'type': source_meta.get('type', 'book'),
                    'extraction': 'auto',
                }],
                'people': people,
                'places': places,
                'tags': ['auto-extracted'],
                'created': TODAY,
                'modified': TODAY,
            }

            new_facts.append(fact)
            fingerprints.add(fp)

    print(f"  Sentences scanned: {sentences_scanned}")
    print(f"  Candidate facts:   {len(new_facts)}")
    print(f"  Skipped (dupes):   {skipped_dupes}")

    if not dry_run and new_facts:
        ensure_dirs()
        with open(FACTS_FILE, 'a', encoding='utf-8') as f:
            for fact in new_facts:
                f.write(json.dumps(fact, ensure_ascii=False) + '\n')
        print(f"  Written {len(new_facts)} facts to {FACTS_FILE}")

    return new_facts


# ── Article extraction ────────────────────────────────────────────────────

def extract_from_article(filepath: Path, source_configs: dict,
                          people_aliases: dict, place_aliases: dict,
                          dry_run: bool = False) -> list[dict]:
    """Extract facts from a markdown article in SOURCES_DIR."""
    # Try to find source config by filename stem
    slug = filepath.stem
    meta = source_configs.get(slug, {
        'id': f'SRC-ART-{slug}',
        'ref': slug,
        'type': 'article',
        'certainty_base': 6,
        'score_threshold': 4,
    })
    return extract_from_file(filepath, meta, dry_run=dry_run,
                              people_aliases=people_aliases,
                              place_aliases=place_aliases)


# ── CLI ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', nargs='?', help='Book markdown file to process')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be extracted without writing')
    parser.add_argument('--all', action='store_true', help='Process all books in BOOKS_DIR')
    parser.add_argument('--articles', action='store_true', help='Process all articles in SOURCES_DIR')
    parser.add_argument('--threshold', type=int, help='Override score threshold for this run')
    args = parser.parse_args()

    source_configs  = load_source_configs()
    people_aliases, place_aliases = load_entity_aliases()

    if args.all:
        books = list(BOOKS_DIR.rglob('*.md'))
        print(f"Processing {len(books)} books...")
        for book in books:
            slug = book.stem
            meta = source_configs.get(slug)
            if not meta:
                print(f"  [skip] {book.name} — no config in config/sources/{slug}.toml")
                continue
            print(f"\n→ {book.name}")
            extract_from_file(book, meta, dry_run=args.dry_run,
                               people_aliases=people_aliases, place_aliases=place_aliases,
                               score_threshold=args.threshold)
        return

    if args.articles:
        articles = list(SOURCES_DIR.glob('*.md'))
        print(f"Processing {len(articles)} articles...")
        for art in articles:
            print(f"\n→ {art.name}")
            extract_from_article(art, source_configs, people_aliases, place_aliases,
                                   dry_run=args.dry_run)
        return

    if not args.source:
        parser.print_help()
        sys.exit(1)

    # Single file
    filepath = Path(args.source)
    if not filepath.exists():
        # Try searching in BOOKS_DIR
        candidate = next(BOOKS_DIR.rglob(args.source if args.source.endswith('.md') else f'{args.source}.md'), None)
        if candidate:
            filepath = candidate
        else:
            print(f"ERROR: File not found: {args.source}", file=sys.stderr)
            sys.exit(1)

    slug = filepath.stem
    meta = source_configs.get(slug)
    if not meta:
        print(f"Warning: no config found for '{slug}' in config/sources/. Using defaults.")
        meta = {
            'id': f'SRC-BOOK-{slug}',
            'ref': slug,
            'type': 'book',
            'certainty_base': 7,
        }

    print(f"Extracting from: {filepath.name}")
    print(f"Source ID: {meta.get('id')}")
    if args.dry_run:
        print("(dry run — nothing will be written)")

    new_facts = extract_from_file(filepath, meta, dry_run=args.dry_run,
                                    people_aliases=people_aliases,
                                    place_aliases=place_aliases,
                                    score_threshold=args.threshold)
    print(f"\nDone. {len(new_facts)} new facts extracted.")


if __name__ == '__main__':
    main()
