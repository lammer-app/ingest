# research-ingest

Unified investigative research pipeline for Claude Code. Ingest articles, books, depositions, and media. Track themes across sources. Query in plain English.

No external dependencies. Python 3.11+ stdlib only.

See [lammer.app/skills/research-ingest](https://lammer.app/skills/research-ingest) for documentation and the copyable skill file.

---

## What it does

Seven subsystems under one command:

| Command | What it does |
|---------|-------------|
| `article <url>` | Paywall bypass cascade → structured source file |
| `doc <path>` | PDF / court document ingestion |
| `book <path>` | EPUB/PDF → multi-agent extraction (Haiku×4 + Sonnet×2) |
| `media <url>` | YouTube / audio → diarized transcript + facts |
| `extract <source>` | Run multi-agent extraction on a saved source file |
| `theme <action>` | Define, tag, suggest, and report on investigative themes |
| `report` | Project status + evidence summaries |

---

## Install

Copy `research-ingest.md` into `.claude/commands/` in your project:

```bash
cp research-ingest.md .claude/commands/research-ingest.md
```

Claude Code picks it up automatically on the next session start.

---

## Requirements

- Python 3.11+ (uses `tomllib` from stdlib)
- SQLite 3.x (included in Python)
- pandoc (optional, for EPUB→markdown conversion)

---

## Quick start

```bash
# Ingest a web article
/research-ingest article https://www.propublica.org/article/...

# Ingest a book
pandoc ~/Downloads/source.epub -t markdown -o books/my-source.md
/research-ingest book books/my-source.md

# Define an investigative theme
/research-ingest theme define "financial-concealment"

# Auto-suggest theme assignments from current fact corpus
/research-ingest theme suggest

# Get evidence summary for a theme
/research-ingest theme report financial-concealment

# Full project status
/research-ingest report
```

---

## Database

Three JSONL files grow as you ingest. Migrate to SQLite for relational queries.

| File | Contains |
|------|----------|
| `facts/facts.jsonl` | Every extracted fact — claim, date, certainty, source, entities, tags, themes |
| `facts/sources.jsonl` | Source registry — title, author, type, certainty tier, ingest date |
| `facts/themes.jsonl` | Investigative threads — keywords, fact IDs, source IDs, date range |

### Fact schema

```json
{
  "id": "F-08910",
  "claim": "...",
  "date": "2026-04-16",
  "certainty": 7,
  "source_id": "ART-dailybeast-maxwell-usb-2026",
  "people": ["Name"],
  "places": ["Location"],
  "orgs": ["Org"],
  "tags": ["label"],
  "themes": ["T-001"]
}
```

### Theme schema

```json
{
  "id": "T-001",
  "name": "financial-concealment",
  "display_name": "Financial Concealment",
  "description": "Methods used to hide assets: shell companies, offshore trusts, nominee accounts",
  "keywords": ["offshore", "shell", "trust", "launder", "conceal", "nominee"],
  "fact_ids": [],
  "source_ids": [],
  "fact_count": 0,
  "source_count": 0,
  "earliest_date": null,
  "latest_date": null,
  "created": "2026-04-27",
  "updated": "2026-04-27",
  "status": "active"
}
```

---

## Project config

Default paths are read from the project's `CLAUDE.md`. To configure a new investigation:

```toml
# ~/.research-ingest/my-project.toml
[project]
name = "my-investigation"
vault_path = "~/research/my-investigation"
facts_path = "{vault_path}/facts/facts.jsonl"
themes_path = "{vault_path}/facts/themes.jsonl"
sources_path = "{vault_path}/facts/sources.jsonl"
sources_dir = "{vault_path}/sources"
```

Pass `--project my-project` to any subsystem command.

---

## Structure

```
investigative-pipeline/
├── research-ingest.md        # Claude Code skill (copy to .claude/commands/)
├── src/
│   ├── extract_facts.py      # sentence scoring + extraction
│   ├── build_registry.py     # sources.jsonl builder
│   ├── generate_views.py     # markdown view generator
│   ├── validate.py           # schema + cross-ref validator
│   ├── migrate_to_sqlite.py  # JSONL → SQLite migration
│   ├── search_facts.py       # query CLI
│   ├── manage_themes.py      # theme CRUD + suggest + report
│   └── build_timeline.py     # chronological timeline builder
├── config/
│   ├── project.toml.example
│   ├── entities.toml.example
│   └── sources/
│       └── example-source.toml
├── db/
│   └── schema.sql            # SQLite schema
├── site/                     # lammer.app/skills documentation
└── vercel.json
```

---

## Article ingestion — strategy cascade

The `article` subsystem tries eight strategies in order:

1. Direct fetch + Trafilatura
2. Jina AI Reader (`r.jina.ai`) ✅ tested working
3. Wayback CDX check → fetch ✅ tested working
4. archive.ph / archive.today (conditional)
5. Syndication hunt (Bloomberg → BritBrief/Yahoo; NYT → Yahoo/MSN)
6. AMP / mobile versions
7. Secondary reconstruction (user permission required)
8. Parallel agent deep research

Known blocked domains (go directly to strategy 2+): `bloomberg.com`, `nytimes.com`, `wsj.com`, `newyorker.com`, `motherjones.com`, `dailymail.co.uk`

---

## Changelog

See [CHANGELOG.md](./CHANGELOG.md) for version history.

Current version: **v1.2** (2026-04-27) — unified skill, theme tracking added

---

## License

MIT
