"""
project_paths.py — Configurable path resolver for investigative-pipeline.

Reads from environment variables (or a .env file if present) so the pipeline
can point at any project without modifying code.

Environment variables:
  PIPELINE_VAULT    — root of your project directory (facts/, sources/, books/, etc.)
  PIPELINE_ARCHIVE  — separate data directory (used when facts live outside the vault)

If PIPELINE_ARCHIVE is not set it defaults to PIPELINE_VAULT/data.

You can also export paths from a .env file:
  export PIPELINE_VAULT=/path/to/my-project
  export PIPELINE_ARCHIVE=/path/to/my-data
  source .env && python3 src/extract_facts.py book.md
"""

import os
from pathlib import Path


def _load_dotenv(path: Path):
    """Minimal .env loader (no external deps). Reads KEY=value lines."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:  # don't override real env vars
            os.environ[key] = value


# Load .env from CWD if present
_load_dotenv(Path.cwd() / ".env")

# ── Path resolution ────────────────────────────────────────────────────────

_DEFAULT_VAULT = Path.cwd()
_DEFAULT_ARCHIVE = _DEFAULT_VAULT / "data"

VAULT   = Path(os.environ.get("PIPELINE_VAULT",   str(_DEFAULT_VAULT))).expanduser().resolve()
ARCHIVE = Path(os.environ.get("PIPELINE_ARCHIVE", str(VAULT / "data"))).expanduser().resolve()

# ── Standard sub-paths ─────────────────────────────────────────────────────

# Source documents (books, articles, transcripts)
BOOKS_DIR   = VAULT / "books"
SOURCES_DIR = VAULT / "sources"
EXTRACTS_DIR = ARCHIVE / "extracts"

# Fact store (JSONL — canonical during early phases)
FACTS_DIR    = ARCHIVE / "facts"
FACTS_FILE   = FACTS_DIR / "facts.jsonl"
SOURCES_FILE = FACTS_DIR / "sources.jsonl"
MEDIA_FILE   = FACTS_DIR / "media.jsonl"
LOG_FILE     = FACTS_DIR / "extraction-log.md"

# Generated views
VIEWS_DIR = FACTS_DIR / "views"

# SQLite evidence DB (populated by migrate_to_sqlite.py)
EVIDENCE_DB = VAULT / "db" / "evidence.db"
SCHEMA_SQL  = VAULT / "db" / "schema.sql"


def ensure_dirs():
    """Create all required directories if they don't exist."""
    for d in [BOOKS_DIR, SOURCES_DIR, EXTRACTS_DIR, FACTS_DIR, VIEWS_DIR, VAULT / "db"]:
        d.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    print(f"VAULT:    {VAULT}")
    print(f"ARCHIVE:  {ARCHIVE}")
    print(f"FACTS:    {FACTS_FILE}")
    print(f"EVIDENCE: {EVIDENCE_DB}")
    print(f"VAULT exists: {VAULT.exists()}")
