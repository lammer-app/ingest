#!/usr/bin/env python3
"""
build_registry.py — Build the source registry (sources.jsonl) from config files.

Scans:
  - config/sources/*.toml   — per-source metadata
  - books/                  — books present in vault
  - sources/                — article markdown files
  - data/extracts/          — document extract directories

Usage:
  python3 src/build_registry.py
  python3 src/build_registry.py --stats
"""

import json
import re
import sys
from datetime import date
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent))
from project_paths import (
    VAULT, BOOKS_DIR, SOURCES_DIR, EXTRACTS_DIR,
    FACTS_FILE, SOURCES_FILE, ensure_dirs
)

TODAY = date.today().isoformat()


# ── TOML loader (same minimal impl as extract_facts.py) ──────────────────

def _load_toml_simple(path: Path) -> dict:
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
            val = val.strip().strip('"').strip("'")
            if val.lower() == "true":
                val = True
            elif val.lower() == "false":
                val = False
            else:
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
    """Load all source configs from config/sources/*.toml. Returns {slug: metadata}."""
    config_dir = VAULT / "config" / "sources"
    sources = {}
    if not config_dir.exists():
        return sources
    for toml_file in sorted(config_dir.glob("*.toml")):
        try:
            data = _load_toml_simple(toml_file)
            src = data.get("source", {})
            slug = toml_file.stem
            sources[slug] = src
        except Exception as e:
            print(f"  Warning: could not parse {toml_file.name}: {e}", file=sys.stderr)
    return sources


# ── Fact count lookup ─────────────────────────────────────────────────────

def count_facts_per_source(facts_file: Path) -> Counter:
    """Count how many facts cite each source_id."""
    counts = Counter()
    if not facts_file.exists():
        return counts
    for line in facts_file.read_text().strip().split('\n'):
        if not line.strip():
            continue
        try:
            fact = json.loads(line)
            for src in fact.get('sources', []):
                sid = src.get('source_id')
                if sid:
                    counts[sid] += 1
        except json.JSONDecodeError:
            pass
    return counts


# ── Book discovery ────────────────────────────────────────────────────────

def discover_books(books_dir: Path) -> list[dict]:
    """Scan books/ directory for markdown files and extract frontmatter."""
    books = []
    if not books_dir.exists():
        return books

    for md_file in sorted(books_dir.rglob('*.md')):
        text = md_file.read_text(encoding='utf-8', errors='replace')
        meta = {'_path': str(md_file), '_slug': md_file.stem}

        # Parse YAML frontmatter if present
        if text.startswith('---'):
            end = text.find('---', 3)
            if end > 0:
                fm = text[3:end]
                for line in fm.splitlines():
                    if ':' in line:
                        k, _, v = line.partition(':')
                        meta[k.strip()] = v.strip().strip('"').strip("'")

        books.append(meta)
    return books


# ── Article discovery ─────────────────────────────────────────────────────

def discover_articles(sources_dir: Path) -> list[dict]:
    """Scan sources/ directory for markdown articles."""
    articles = []
    if not sources_dir.exists():
        return articles
    for md_file in sorted(sources_dir.glob('*.md')):
        articles.append({'_path': str(md_file), '_slug': md_file.stem})
    return articles


# ── Registry builder ──────────────────────────────────────────────────────

def build_registry(dry_run: bool = False) -> list[dict]:
    """Build sources.jsonl from config files + vault discovery."""
    source_configs = load_source_configs()
    fact_counts    = count_facts_per_source(FACTS_FILE)
    books          = discover_books(BOOKS_DIR)
    articles       = discover_articles(SOURCES_DIR)

    registry = []
    seen_ids = set()

    # 1. Sources defined in config files (primary source of truth)
    for slug, cfg in source_configs.items():
        source_id = cfg.get('id', f'SRC-UNKNOWN-{slug}')
        if source_id in seen_ids:
            continue
        seen_ids.add(source_id)

        # Check if vault file exists
        vault_path = None
        for book in books:
            if book['_slug'] == slug:
                vault_path = book['_path']
                break

        entry = {
            'id':                source_id,
            'title':             cfg.get('title') or cfg.get('ref', slug),
            'author':            cfg.get('author', 'Unknown'),
            'year':              cfg.get('year'),
            'type':              cfg.get('type', 'book'),
            'certainty_base':    cfg.get('certainty_base', 7),
            'is_memoir':         cfg.get('is_memoir', False),
            'extraction_status': 'complete' if fact_counts.get(source_id, 0) > 0 else 'pending',
            'facts_extracted':   fact_counts.get(source_id, 0),
            'vault_path':        vault_path,
            'notes':             cfg.get('notes', ''),
            'updated':           TODAY,
        }
        if 'ref' in cfg:
            entry['ref'] = cfg['ref']

        registry.append(entry)

    # 2. Books found in vault without a config (auto-discovered)
    for book in books:
        slug = book['_slug']
        # Skip if already registered via config
        if any(e.get('vault_path') == book['_path'] for e in registry):
            continue
        source_id = f'SRC-BOOK-{slug}'
        if source_id in seen_ids:
            continue
        seen_ids.add(source_id)

        entry = {
            'id':                source_id,
            'title':             book.get('title', slug),
            'author':            book.get('author', 'Unknown'),
            'year':              book.get('year'),
            'type':              'book',
            'extraction_status': 'complete' if fact_counts.get(source_id, 0) > 0 else 'pending',
            'facts_extracted':   fact_counts.get(source_id, 0),
            'vault_path':        book['_path'],
            'notes':             'auto-discovered (no config file)',
            'updated':           TODAY,
        }
        registry.append(entry)

    # 3. Articles found in sources/ without a config (auto-discovered)
    for article in articles:
        slug = article['_slug']
        source_id = f'SRC-ART-{slug}'
        if source_id in seen_ids:
            continue
        seen_ids.add(source_id)

        entry = {
            'id':                source_id,
            'title':             slug.replace('-', ' ').title(),
            'type':              'article',
            'extraction_status': 'complete' if fact_counts.get(source_id, 0) > 0 else 'pending',
            'facts_extracted':   fact_counts.get(source_id, 0),
            'vault_path':        article['_path'],
            'notes':             'auto-discovered (no config file)',
            'updated':           TODAY,
        }
        registry.append(entry)

    print(f"Registry: {len(registry)} sources ({len(source_configs)} configured, "
          f"{len(books)} books, {len(articles)} articles)")
    print(f"  Complete: {sum(1 for e in registry if e['extraction_status'] == 'complete')}")
    print(f"  Pending:  {sum(1 for e in registry if e['extraction_status'] == 'pending')}")

    if not dry_run:
        ensure_dirs()
        with open(SOURCES_FILE, 'w', encoding='utf-8') as f:
            for entry in registry:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        print(f"Written to {SOURCES_FILE}")

    return registry


def main():
    dry_run = '--dry-run' in sys.argv
    stats_only = '--stats' in sys.argv

    if stats_only:
        configs = load_source_configs()
        counts  = count_facts_per_source(FACTS_FILE)
        print(f"Configured sources: {len(configs)}")
        print(f"Total facts in db:  {sum(counts.values())}")
        print(f"Unique sources cited: {len(counts)}")
        return

    build_registry(dry_run=dry_run)


if __name__ == '__main__':
    main()
