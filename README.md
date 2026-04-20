# investigative-pipeline

Source ingestion and fact extraction pipeline for long-form investigative research.

No external dependencies. Python 3.11+ stdlib only. Bring your own sources.

## What it does

Extracts structured factual claims from research sources (books, articles, depositions, transcripts) into a JSONL fact store, then migrates to SQLite for timeline generation, entity querying, and theme analysis.

## Requirements

- Python 3.11+ (uses `tomllib` from stdlib)
- SQLite 3.x (included in Python)
- pandoc (optional, for EPUB→markdown conversion)

## Quick start

```bash
# Configure paths
cp config/project.toml.example .env
# Edit .env: set PIPELINE_VAULT and PIPELINE_ARCHIVE
source .env

# Add a source config
cp config/sources/example-source.toml config/sources/my-source.toml

# Convert EPUB to markdown (optional)
pandoc ~/Downloads/source.epub -t markdown -o books/my-source.md

# Extract facts
python3 src/extract_facts.py books/my-source.md

# Build registry and validate
python3 src/build_registry.py
python3 src/validate.py

# Generate views and timeline
python3 src/generate_views.py
python3 src/build_timeline.py

# Migrate to SQLite
python3 src/migrate_to_sqlite.py

# Search
python3 src/search_facts.py "query" --full
python3 src/search_facts.py --person "Name" --year 2005 --certainty 7
```

## Structure

```
investigative-pipeline/
├── src/
│   ├── project_paths.py      # env-based path config
│   ├── extract_facts.py      # sentence scoring + extraction
│   ├── build_registry.py     # sources.jsonl builder
│   ├── generate_views.py     # markdown view generator
│   ├── validate.py           # schema + cross-ref validator
│   ├── migrate_to_sqlite.py  # JSONL → SQLite migration
│   ├── search_facts.py       # query CLI
│   └── build_timeline.py     # chronological timeline builder
├── config/
│   ├── project.toml.example  # path + extraction settings
│   ├── entities.toml.example # entity alias configuration
│   └── sources/
│       └── example-source.toml
├── db/
│   └── schema.sql            # SQLite schema (13 tables)
├── site/                     # documentation site (Vercel)
│   ├── index.html
│   └── style.css
└── vercel.json
```

## Configuration

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PIPELINE_VAULT` | `.` (cwd) | Project root — contains books/, sources/, config/, db/ |
| `PIPELINE_ARCHIVE` | `$VAULT/data` | Data directory — contains facts/, views/ |

Set in `.env` or export directly. See `config/project.toml.example`.

### Source configs (`config/sources/*.toml`)

One TOML file per source. The filename stem is the slug that maps to a file in `books/` or `sources/`.

```toml
[source]
id             = "SRC-BOOK-author-slug"
ref            = "Author, Title (Year)"
type           = "book"
author         = "Author Name"
year           = 2022
certainty_base = 7
is_memoir      = false
notes          = ""
```

### Entity aliases (`config/entities.toml`)

```toml
[people.doe-john]
canonical = "John Doe"
aliases   = ["J. Doe", "Johnny Doe"]
```

### Themes (`src/search_facts.py` and `src/migrate_to_sqlite.py`)

Edit the `THEMES` dict in each script to define interpretive lenses for your project:

```python
THEMES = {
    "financier": "accounts, transfers, investments, funds",
    "network":   "associates, connections, meetings",
}
```

## Fact schema

```json
{
  "id":            "F-2005-0042",
  "claim":         "...",
  "date":          { "value": "2005-03-14", "precision": "exact" },
  "certainty":     8,
  "status":        "confirmed",
  "review_status": "reviewed",
  "sources":       [{ "source_id": "SRC-BOOK-slug", "page": "142" }],
  "people":        ["Name"],
  "places":        ["Location"],
  "organizations": ["Org Name"],
  "tags":          ["financial"],
  "created":       "2025-01-15",
  "modified":      "2025-01-15"
}
```

## License

Private.
