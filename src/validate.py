#!/usr/bin/env python3
"""
validate.py — Validate facts.jsonl, media.jsonl, and sources.jsonl against schemas.

Checks:
  - JSON validity
  - Required fields present
  - ID format correctness
  - Cross-reference integrity (fact→media, fact→source, media→fact)
  - Duplicate detection
  - Orphan detection

Usage:
  python3 src/validate.py            # Full validation
  python3 src/validate.py --fix      # Auto-fix trivial issues (missing modified dates, etc.)
"""

import json
import sys
import re
from datetime import date
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent))
from project_paths import FACTS_FILE, MEDIA_FILE, SOURCES_FILE

TODAY = date.today().isoformat()

# ── Schema definitions ────────────────────────────────────────────────────────

FACT_REQUIRED = ['id', 'claim', 'date', 'certainty', 'status', 'sources', 'review_status']
FACT_ID_RE = re.compile(r'^F-(\d{4}|XXXX|[A-Z]{4,})-(\d{4,}[A-Z]?|[A-Z]+-\d{3,})$')
FACT_STATUSES = {'alleged', 'confirmed', 'disproven', 'disputed'}
FACT_REVIEW_STATUSES = {'draft', 'reviewed', 'final'}
FACT_DATE_PRECISIONS = {'exact', 'month', 'year', 'range', 'undated', 'approximate'}

MEDIA_REQUIRED = ['id', 'type', 'description', 'review_status']
MEDIA_ID_RE = re.compile(r'^M-(IMG|VID|AUD|DOC)-\d{4}$')
MEDIA_TYPES = {'image', 'video', 'audio', 'document'}

SOURCE_REQUIRED = ['id', 'type', 'title', 'extraction_status']
SOURCE_ID_RE = re.compile(r'^SRC-\w+[\w-]+$')


class Validator:
    def __init__(self, fix=False):
        self.fix = fix
        self.errors = []
        self.warnings = []
        self.fixes = []

    def validate_all(self):
        """Run all validations."""
        facts   = self._load_and_validate_jsonl(FACTS_FILE,   'fact')
        media   = self._load_and_validate_jsonl(MEDIA_FILE,   'media')
        sources = self._load_and_validate_jsonl(SOURCES_FILE, 'source')

        if facts is not None:
            self._validate_facts(facts)
        if media is not None:
            self._validate_media(media)
        if sources is not None:
            self._validate_sources(sources)

        # Cross-reference checks
        if facts is not None and media is not None:
            self._check_cross_refs(facts, media, sources or [])

        # Duplicate checks
        if facts is not None:
            self._check_duplicates(facts, 'fact')
        if media is not None:
            self._check_duplicates(media, 'media')

        self._report()

        if self.fix and self.fixes:
            self._apply_fixes(facts, media, sources)

    def _load_and_validate_jsonl(self, filepath, entity_type):
        """Load JSONL file and validate JSON syntax."""
        if not filepath.exists():
            if entity_type == 'source':
                self.warnings.append(
                    f"{filepath.name} does not exist yet "
                    f"(run build_registry.py to create it)"
                )
                return None
            self.errors.append(f"{filepath.name} does not exist")
            return None

        entries = []
        for i, line in enumerate(filepath.read_text().strip().split('\n'), 1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                entry['_line'] = i
                entries.append(entry)
            except json.JSONDecodeError as e:
                self.errors.append(f"{filepath.name} line {i}: Invalid JSON — {e}")

        return entries

    def _validate_facts(self, facts):
        """Validate fact entries."""
        for fact in facts:
            line = fact.get('_line', '?')
            fid  = fact.get('id', 'NO_ID')

            # Required fields
            for field in FACT_REQUIRED:
                if field not in fact:
                    self.errors.append(
                        f"Fact {fid} (line {line}): Missing required field '{field}'"
                    )

            # ID format
            if 'id' in fact and not FACT_ID_RE.match(fact['id']):
                self.errors.append(
                    f"Fact {fid} (line {line}): Invalid ID format "
                    f"(expected F-YYYY-NNNN or F-XXXX-slug)"
                )

            # Status
            if fact.get('status') and fact['status'] not in FACT_STATUSES:
                self.errors.append(
                    f"Fact {fid} (line {line}): Invalid status '{fact['status']}'"
                )

            # Review status
            if fact.get('review_status') and fact['review_status'] not in FACT_REVIEW_STATUSES:
                self.errors.append(
                    f"Fact {fid} (line {line}): Invalid review_status '{fact['review_status']}'"
                )

            # Date
            if 'date' in fact:
                d = fact['date']
                if not isinstance(d, dict):
                    self.errors.append(f"Fact {fid} (line {line}): 'date' must be an object")
                elif 'value' not in d:
                    self.errors.append(f"Fact {fid} (line {line}): date missing 'value'")
                elif 'precision' not in d:
                    self.warnings.append(f"Fact {fid} (line {line}): date missing 'precision'")
                elif d['precision'] not in FACT_DATE_PRECISIONS:
                    self.warnings.append(
                        f"Fact {fid} (line {line}): Unknown date precision '{d['precision']}'"
                    )

            # Certainty range
            if 'certainty' in fact:
                c = fact['certainty']
                if not isinstance(c, (int, float)) or c < 0 or c > 10:
                    self.errors.append(
                        f"Fact {fid} (line {line}): Certainty must be 0–10, got {c}"
                    )

            # Claim length
            if 'claim' in fact:
                if len(fact['claim']) < 10:
                    self.warnings.append(
                        f"Fact {fid} (line {line}): Very short claim ({len(fact['claim'])} chars)"
                    )
                elif len(fact['claim']) > 1000:
                    self.warnings.append(
                        f"Fact {fid} (line {line}): Very long claim ({len(fact['claim'])} chars)"
                    )

            # Sources must be an array
            if 'sources' in fact and not isinstance(fact['sources'], list):
                self.errors.append(f"Fact {fid} (line {line}): 'sources' must be an array")

            # Missing timestamps
            if not fact.get('created'):
                self.fixes.append(('fact', fid, 'created', TODAY))
            if not fact.get('modified'):
                self.fixes.append(('fact', fid, 'modified', TODAY))

    def _validate_media(self, media):
        """Validate media entries."""
        for m in media:
            line = m.get('_line', '?')
            mid  = m.get('id', 'NO_ID')

            # Required fields
            for field in MEDIA_REQUIRED:
                if field not in m:
                    self.errors.append(
                        f"Media {mid} (line {line}): Missing required field '{field}'"
                    )

            # ID format
            if 'id' in m and not MEDIA_ID_RE.match(m['id']):
                self.errors.append(
                    f"Media {mid} (line {line}): Invalid ID format (expected M-TYPE-NNNN)"
                )

            # Type
            if m.get('type') and m['type'] not in MEDIA_TYPES:
                self.errors.append(
                    f"Media {mid} (line {line}): Invalid type '{m['type']}'"
                )

            # File extension check (warning only)
            if m.get('file_path'):
                if not m['file_path'].lower().endswith((
                    '.jpg', '.jpeg', '.png', '.webp', '.gif',
                    '.mp4', '.mov', '.mp3', '.wav',
                    '.pdf', '.doc', '.docx',
                )):
                    self.warnings.append(
                        f"Media {mid} (line {line}): Unusual file extension in path"
                    )

    def _validate_sources(self, sources):
        """Validate source registry entries."""
        for s in sources:
            line = s.get('_line', '?')
            sid  = s.get('id', 'NO_ID')

            for field in SOURCE_REQUIRED:
                if field not in s:
                    self.errors.append(
                        f"Source {sid} (line {line}): Missing required field '{field}'"
                    )

            if 'id' in s and not SOURCE_ID_RE.match(s['id']):
                self.errors.append(
                    f"Source {sid} (line {line}): Invalid ID format (expected SRC-TYPE-slug)"
                )

    def _check_cross_refs(self, facts, media, sources):
        """Check referential integrity across entities."""
        media_ids  = {m['id'] for m in media   if 'id' in m}
        fact_ids   = {f['id'] for f in facts   if 'id' in f}
        source_ids = {s['id'] for s in sources if 'id' in s}

        # Facts referencing non-existent media
        for f in facts:
            for mid in f.get('media', []):
                if mid not in media_ids:
                    self.warnings.append(
                        f"Fact {f['id']}: References media '{mid}' which doesn't exist"
                    )

        # Media referencing non-existent facts
        for m in media:
            for fid in m.get('linked_facts', []):
                if fid not in fact_ids:
                    self.warnings.append(
                        f"Media {m['id']}: References fact '{fid}' which doesn't exist"
                    )

        # Facts citing sources not in registry (warning only — registry may be incomplete)
        if source_ids:
            for f in facts:
                for src in f.get('sources', []):
                    sid = src.get('source_id')
                    if sid and sid not in source_ids:
                        self.warnings.append(
                            f"Fact {f['id']}: Cites source '{sid}' not in sources.jsonl"
                        )

    def _check_duplicates(self, entries, entity_type):
        """Check for duplicate IDs."""
        id_counts = Counter(e.get('id', 'NO_ID') for e in entries)
        for eid, count in id_counts.items():
            if count > 1:
                self.errors.append(
                    f"Duplicate {entity_type} ID: {eid} appears {count} times"
                )

    def _report(self):
        """Print validation report."""
        print(f"\n═══ Validation Report ═══\n")

        if self.errors:
            print(f"ERRORS ({len(self.errors)}):")
            for e in self.errors[:50]:
                print(f"  {e}")
            if len(self.errors) > 50:
                print(f"  ... and {len(self.errors) - 50} more")
            print()

        if self.warnings:
            print(f"WARNINGS ({len(self.warnings)}):")
            for w in self.warnings[:30]:
                print(f"  {w}")
            if len(self.warnings) > 30:
                print(f"  ... and {len(self.warnings) - 30} more")
            print()

        if self.fixes:
            print(f"AUTO-FIXABLE ({len(self.fixes)}):")
            for f in self.fixes[:10]:
                print(f"  {f[0]} {f[1]}: set {f[2]} = {f[3]}")
            if len(self.fixes) > 10:
                print(f"  ... and {len(self.fixes) - 10} more")
            print()

        if not self.errors and not self.warnings:
            print("All checks passed!")
        elif not self.errors:
            print(f"No errors (but {len(self.warnings)} warnings)")
        else:
            print(f"{len(self.errors)} error(s) found")

    def _apply_fixes(self, facts, media, sources):
        """Apply auto-fixes to JSONL files."""
        if not facts:
            return

        # Build fix lookup
        fact_fixes = {}
        for entity_type, eid, field, value in self.fixes:
            if entity_type == 'fact':
                if eid not in fact_fixes:
                    fact_fixes[eid] = {}
                fact_fixes[eid][field] = value

        # Apply to facts
        changed = 0
        for fact in facts:
            fid = fact.get('id')
            if fid in fact_fixes:
                for field, value in fact_fixes[fid].items():
                    fact[field] = value
                    changed += 1

        if changed:
            # Remove _line metadata before writing
            clean_facts = [{k: v for k, v in f.items() if k != '_line'} for f in facts]
            with open(FACTS_FILE, 'w') as out:
                for fact in clean_facts:
                    out.write(json.dumps(fact, ensure_ascii=False) + '\n')
            print(f"\nApplied {changed} fixes to {FACTS_FILE.name}")


def main():
    fix = '--fix' in sys.argv
    validator = Validator(fix=fix)
    validator.validate_all()


if __name__ == '__main__':
    main()
