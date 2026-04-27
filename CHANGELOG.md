# Ingest Pipeline — Changelog

Private log of major revisions and new features for the `/ingest` skill and `lammer.app/ingest` pipeline.

---

## Current State

**Skill location:** `.claude/commands/research-ingest.md` (v1.2)
**Legacy skill:** `.claude/commands/ingest-article.md` (deprecated — deleted v1.2)
**Public tool:** https://lammer.app/skills
**Database:** `03_Resources/Epstein/` (JSONL + SQLite)

---

## v1.2 — research-ingest: Unified Skill (2026-04-27)

Merged `ingest-article` and `multi-agent-document-pipeline` into a single unified skill called `research-ingest`. Added theme tracking system.

### New skill: `research-ingest`
Single entry point with subsystem routing:
- `article <url>` — full bypass cascade (was: `/ingest-article`)
- `doc <path>` — PDF/court document ingestion
- `book <path>` — EPUB/PDF → multi-agent extraction (was: separate skill)
- `media <url>` — YouTube/audio → diarized transcript
- `extract <source>` — run multi-agent pipeline on saved source
- `theme <action>` — theme tracking (new)
- `report` — project status

### Theme tracking (new)
Investigative threads that accumulate evidence across sources. Distinct from `tags`.

**Schema:** `themes.jsonl` with id, name, description, keywords[], fact_ids[], source_ids[], fact_count, source_count, date range, status

**Workflow:**
- `theme define` — create a theme with keywords
- `theme tag` — manually assign facts to themes
- `theme suggest` — auto-propose taggings via keyword matching + Claude classification
- `theme report` — structured evidence summary (fact count, source coverage, date range, gaps)

**Facts gain `themes: [theme_id]` field** alongside existing `tags`.

### Multi-agent extraction integrated
`book` and `extract` subsystems now include the full 6-agent pipeline (Haiku×4 + Sonnet×2) from the `multi-agent-document-pipeline` skill. That skill remains as a standalone reference for architecture docs.

### Project-agnostic config
`--project <name>` flag reads from `~/.research-ingest/<name>.toml`. Default still uses Epstein vault paths (backward-compatible).

### Deprecations
- `ingest-article.md` marked deprecated in v1.2, deleted in v1.2 final
