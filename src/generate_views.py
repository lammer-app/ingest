#!/usr/bin/env python3
"""
generate_views.py — Generate browsable markdown views from facts.jsonl.

Reads JSONL files and generates read-only markdown views organized by:
  - year, entity, source, topic/tag, status, certainty
  - coverage dashboards
  - stats

Usage:
  python3 src/generate_views.py           # Regenerate all views
  python3 src/generate_views.py --stats   # Just print stats
"""

import json
import sys
from datetime import date
from pathlib import Path
from collections import defaultdict, Counter

sys.path.insert(0, str(Path(__file__).parent))
from project_paths import FACTS_FILE, MEDIA_FILE, SOURCES_FILE, VIEWS_DIR, ensure_dirs

TODAY = date.today().isoformat()


def load_jsonl(filepath: Path) -> list[dict]:
    entries = []
    if not filepath.exists():
        return entries
    for line in filepath.read_text().strip().split('\n'):
        if line.strip():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries


def format_fact_row(fact: dict, include_chapter: bool = False) -> str:
    fid     = fact['id']
    claim   = fact['claim'][:120].replace('|', '\\|').replace('\n', ' ')
    if len(fact['claim']) > 120:
        claim += '...'
    certainty = fact.get('certainty', '?')
    status    = fact.get('status', '?')
    entities  = ', '.join(fact.get('people', [])[:3])
    if len(fact.get('people', [])) > 3:
        entities += f" +{len(fact['people']) - 3}"
    sources_count = len(fact.get('sources', []))
    row = f"| `{fid}` | {claim} | {certainty} | {status} | {entities} | {sources_count} |"
    if include_chapter:
        chapters = ', '.join(fact.get('appears_in', []))
        row += f" {chapters} |"
    return row


def fact_table_header(include_chapter: bool = False) -> str:
    header    = "| ID | Claim | Cert | Status | Entities | Sources |"
    separator = "|---|---|---|---|---|---|"
    if include_chapter:
        header    += " Chapter |"
        separator += "---|"
    return header + "\n" + separator


def format_fact_detail(fact: dict) -> str:
    lines = []
    lines.append(f"### `{fact['id']}`")
    lines.append("")
    lines.append(f"**{fact['claim']}**")
    lines.append("")

    d = fact.get('date', {})
    date_str  = d.get('value', 'undated')
    precision = d.get('precision', 'unknown')
    lines.append(f"- **Date:** {date_str} ({precision})")
    lines.append(f"- **Certainty:** {fact.get('certainty', '?')}/10")
    lines.append(f"- **Status:** {fact.get('status', '?')}")

    sources = fact.get('sources', [])
    if sources:
        lines.append(f"- **Sources ({len(sources)}):**")
        for s in sources:
            ref    = s.get('ref', 'Unknown')
            sid    = s.get('source_id', '')
            quote  = s.get('quote', '')
            src_ln = f"  - {ref}"
            if s.get('page'):
                src_ln += f" (p. {s['page']})"
            if s.get('timestamp'):
                src_ln += f" [{s['timestamp']}]"
            if sid:
                src_ln += f" (`{sid}`)"
            if quote:
                src_ln += f' — "{quote}"'
            lines.append(src_ln)

    if fact.get('people'):
        lines.append(f"- **People:** {', '.join(fact['people'])}")
    if fact.get('places'):
        lines.append(f"- **Places:** {', '.join(fact['places'])}")
    if fact.get('tags'):
        lines.append(f"- **Tags:** {', '.join(fact['tags'])}")
    if fact.get('appears_in'):
        lines.append(f"- **Appears in:** {', '.join(fact['appears_in'])}")
    if fact.get('commentary'):
        lines.append(f"- **Commentary:** {fact['commentary']}")
    if fact.get('disputed_by'):
        lines.append(f"- **Disputed by:** {fact['disputed_by']}")

    lines.append("")
    lines.append("---")
    lines.append("")
    return '\n'.join(lines)


class ViewGenerator:
    def __init__(self):
        self.facts   = load_jsonl(FACTS_FILE)
        self.media   = load_jsonl(MEDIA_FILE)
        self.sources = load_jsonl(SOURCES_FILE)

    def generate_all(self):
        ensure_dirs()
        print(f"Loaded {len(self.facts)} facts, {len(self.media)} media, {len(self.sources)} sources")

        self.generate_index()
        self.generate_by_year()
        self.generate_by_entity()
        self.generate_by_source()
        self.generate_by_topic()
        self.generate_by_status()
        self.generate_by_certainty()
        self.generate_by_chapter()
        self.generate_media_views()
        self.generate_coverage()
        self.generate_stats()

        print("All views generated.")

    def generate_index(self):
        year_counts   = Counter()
        status_counts = Counter()
        cert_counts   = {'low (0-4)': 0, 'medium (5-7)': 0, 'high (8-10)': 0}

        for f in self.facts:
            year = (f.get('date', {}).get('value') or 'undated')[:4]
            year_counts[year] += 1
            status_counts[f.get('status', 'unknown')] += 1
            c = f.get('certainty', 0)
            if not isinstance(c, (int, float)):
                c = 0
            if c <= 4:
                cert_counts['low (0-4)'] += 1
            elif c <= 7:
                cert_counts['medium (5-7)'] += 1
            else:
                cert_counts['high (8-10)'] += 1

        content = f"# Facts Database — Index\n\n*Auto-generated {TODAY} — do not hand-edit*\n\n"
        content += "## Summary\n\n| Metric | Count |\n|--------|-------|\n"
        content += f"| Total facts | {len(self.facts)} |\n"
        content += f"| Total media | {len(self.media)} |\n"
        content += f"| Total sources | {len(self.sources)} |\n\n"

        content += "## By Status\n\n| Status | Count |\n|--------|-------|\n"
        for status, count in sorted(status_counts.items()):
            content += f"| {status} | {count} |\n"

        content += "\n## By Certainty\n\n| Band | Count |\n|------|-------|\n"
        for band, count in cert_counts.items():
            content += f"| {band} | {count} |\n"

        content += "\n## By Year\n\n| Year | Count | Link |\n|------|-------|------|\n"
        for year in sorted(year_counts.keys()):
            count = year_counts[year]
            link  = f"[→](by-year/{year}.md)" if year not in ('unda', 'XXXX') else "[→](by-year/undated.md)"
            content += f"| {year} | {count} | {link} |\n"

        content += "\n## Views\n\n"
        content += "- [By Year](by-year/) — Facts organized chronologically\n"
        content += "- [By Entity](by-entity/) — Facts about specific people\n"
        content += "- [By Source](by-source/) — What each source contributed\n"
        content += "- [By Topic](by-topic/) — Thematic groupings\n"
        content += "- [By Status](by-status/) — Disputed, needs verification, etc.\n"
        content += "- [By Certainty](by-certainty/) — Low-certainty claims needing more sourcing\n"
        content += "- [By Chapter](by-chapter/) — Facts as they appear in the work\n"
        content += "- [Media](media/) — Images, video, audio, documents\n"
        content += "- [Coverage](coverage/) — Source extraction progress\n"
        content += "- [Stats](stats.md) — Detailed statistics\n"

        self._write("index.md", content)

    def generate_by_year(self):
        by_year = defaultdict(list)
        for f in self.facts:
            year = (f.get('date', {}).get('value') or 'undated')[:4]
            if year in ('unda',):
                year = 'undated'
            by_year[year].append(f)

        for year, facts in sorted(by_year.items()):
            filename = "undated.md" if year in ('undated', 'XXXX') else f"{year}.md"
            title    = "Undated"   if year in ('undated', 'XXXX') else year
            content  = f"# Facts: {title}\n\n*{len(facts)} facts — auto-generated {TODAY}*\n\n"

            prec_order = {'exact': 0, 'month': 1, 'year': 2, 'approximate': 3, 'undated': 4}
            facts.sort(key=lambda f: (
                prec_order.get(f.get('date', {}).get('precision', 'undated'), 4),
                f.get('date', {}).get('value') or 'zzz',
            ))
            for fact in facts:
                content += format_fact_detail(fact)
            self._write(f"by-year/{filename}", content)

        print(f"  by-year: {len(by_year)} files")

    def generate_by_entity(self):
        by_entity = defaultdict(list)
        for f in self.facts:
            for person in f.get('people', []):
                by_entity[person].append(f)
            for place in f.get('places', []):
                by_entity[place].append(f)

        for entity, facts in sorted(by_entity.items()):
            content = f"# Facts: {entity}\n\n*{len(facts)} facts — auto-generated {TODAY}*\n\n"
            facts.sort(key=lambda f: f.get('date', {}).get('value') or 'zzz')
            for fact in facts:
                content += format_fact_detail(fact)
            safe = re.sub(r'[^\w-]', '_', entity)
            self._write(f"by-entity/{safe}.md", content)

        print(f"  by-entity: {len(by_entity)} files")

    def generate_by_source(self):
        by_source = defaultdict(list)
        for f in self.facts:
            for src in f.get('sources', []):
                sid = src.get('source_id', 'unknown')
                by_source[sid].append(f)

        for source_id, facts in sorted(by_source.items()):
            source_info = next((s for s in self.sources if s.get('id') == source_id), None)
            title = source_info.get('title', source_id) if source_info else source_id

            content = f"# Source: {title}\n\n"
            if source_info:
                content += f"- **Author:** {source_info.get('author', 'Unknown')}\n"
                content += f"- **Year:** {source_info.get('year', 'Unknown')}\n"
                content += f"- **Type:** {source_info.get('type', 'Unknown')}\n"
                content += f"- **Extraction status:** {source_info.get('extraction_status', 'unknown')}\n\n"

            content += f"*{len(facts)} facts from this source — auto-generated {TODAY}*\n\n"
            facts.sort(key=lambda f: f.get('date', {}).get('value') or 'zzz')
            for fact in facts:
                content += format_fact_detail(fact)

            safe = source_id.replace('/', '-')
            self._write(f"by-source/{safe}.md", content)

        print(f"  by-source: {len(by_source)} files")

    def generate_by_topic(self):
        by_tag = defaultdict(list)
        for f in self.facts:
            for tag in f.get('tags', []):
                by_tag[tag].append(f)

        for tag, facts in sorted(by_tag.items()):
            content = f"# Topic: {tag}\n\n*{len(facts)} facts — auto-generated {TODAY}*\n\n"
            facts.sort(key=lambda f: f.get('date', {}).get('value') or 'zzz')
            content += fact_table_header(include_chapter=True) + "\n"
            for fact in facts:
                content += format_fact_row(fact, include_chapter=True) + "\n"
            self._write(f"by-topic/{tag}.md", content)

        print(f"  by-topic: {len(by_tag)} files")

    def generate_by_status(self):
        by_status = defaultdict(list)
        for f in self.facts:
            by_status[f.get('status', 'unknown')].append(f)

        for status, facts in by_status.items():
            content = f"# Status: {status}\n\n*{len(facts)} facts — auto-generated {TODAY}*\n\n"
            for fact in facts:
                content += format_fact_detail(fact)
            self._write(f"by-status/{status}.md", content)

        # Needs verification
        needs_verify = [f for f in self.facts if 'needs-verification' in f.get('tags', [])]
        content = f"# Facts Needing Verification\n\n*{len(needs_verify)} facts — auto-generated {TODAY}*\n\n"
        for fact in needs_verify:
            content += format_fact_detail(fact)
        self._write("by-status/needs-verification.md", content)

        print(f"  by-status: {len(by_status) + 1} files")

    def generate_by_certainty(self):
        low_cert = [f for f in self.facts if f.get('certainty', 10) < 5]
        low_cert.sort(key=lambda f: f.get('certainty', 0))
        content = f"# Low-Certainty Facts (< 5)\n\n*{len(low_cert)} facts — auto-generated {TODAY}*\n\n"
        for fact in low_cert:
            content += format_fact_detail(fact)
        self._write("by-certainty/low-certainty.md", content)
        print(f"  by-certainty: 1 file")

    def generate_by_chapter(self):
        by_chapter = defaultdict(list)
        for f in self.facts:
            for ch in f.get('appears_in', []):
                by_chapter[ch].append(f)

        for chapter, facts in sorted(by_chapter.items()):
            slug    = Path(chapter).stem
            content = f"# Facts from: {chapter}\n\n*{len(facts)} facts — auto-generated {TODAY}*\n\n"
            for fact in facts:
                content += format_fact_detail(fact)
            self._write(f"by-chapter/{slug}.md", content)

        print(f"  by-chapter: {len(by_chapter)} files")

    def generate_media_views(self):
        content = f"# Media Index\n\n*{len(self.media)} items — auto-generated {TODAY}*\n\n"
        type_counts = Counter(m.get('type', 'unknown') for m in self.media)
        content += "## By Type\n\n| Type | Count |\n|------|-------|\n"
        for t, c in sorted(type_counts.items()):
            content += f"| {t} | {c} |\n"
        self._write("media/index.md", content)

        for media_type in ('image', 'video', 'audio', 'document'):
            items = [m for m in self.media if m.get('type') == media_type]
            content = f"# {media_type.title()}s\n\n*{len(items)} items — auto-generated {TODAY}*\n\n"
            for item in items:
                content += f"### `{item['id']}`\n\n"
                content += f"**{item.get('description', 'No description')[:200]}**\n\n"
                if item.get('file_path'):
                    content += f"- Path: `{item['file_path']}`\n"
                if item.get('linked_facts'):
                    content += f"- Linked facts: {', '.join(item['linked_facts'])}\n"
                content += "\n---\n\n"
            self._write(f"media/{media_type}s.md", content)

        unlinked = [m for m in self.media if not m.get('linked_facts')]
        content  = f"# Unlinked Media\n\n*{len(unlinked)} items — auto-generated {TODAY}*\n\n"
        for m in unlinked:
            content += f"- `{m['id']}` ({m.get('type', '?')}) — {m.get('description', '')[:100]}\n"
        self._write("media/unlinked.md", content)
        print(f"  media: 6 files")

    def generate_coverage(self):
        if not self.sources:
            placeholder = f"# Source Coverage\n\n*No sources.jsonl yet — run build_registry.py first*\n"
            self._write("coverage/sources-pending.md", placeholder)
            self._write("coverage/sources-complete.md", placeholder)
            print(f"  coverage: 2 placeholder files")
            return

        pending  = [s for s in self.sources if s.get('extraction_status') != 'complete']
        complete = [s for s in self.sources if s.get('extraction_status') == 'complete']

        content = f"# Sources Pending Extraction\n\n*{len(pending)} sources — auto-generated {TODAY}*\n\n"
        content += "| Source ID | Title | Type | Status |\n|---|---|---|---|\n"
        for s in pending:
            content += f"| `{s['id']}` | {s.get('title', '?')} | {s.get('type', '?')} | {s.get('extraction_status', 'pending')} |\n"
        self._write("coverage/sources-pending.md", content)

        content = f"# Sources Fully Extracted\n\n*{len(complete)} sources — auto-generated {TODAY}*\n\n"
        content += "| Source ID | Title | Type | Facts |\n|---|---|---|---|\n"
        for s in complete:
            content += f"| `{s['id']}` | {s.get('title', '?')} | {s.get('type', '?')} | {s.get('facts_extracted', 0)} |\n"
        self._write("coverage/sources-complete.md", content)
        print(f"  coverage: 2 files")

    def generate_stats(self):
        people_counter = Counter()
        tag_counter    = Counter()
        source_counter = Counter()
        for f in self.facts:
            for p in f.get('people', []):
                people_counter[p] += 1
            for t in f.get('tags', []):
                tag_counter[t] += 1
            for s in f.get('sources', []):
                source_counter[s.get('source_id', 'unknown')] += 1

        content = f"# Facts Database Statistics\n\n*Auto-generated {TODAY}*\n\n"
        content += f"## Overview\n\n"
        content += f"- **Total facts:** {len(self.facts)}\n"
        content += f"- **Total media:** {len(self.media)}\n"
        content += f"- **Total sources in registry:** {len(self.sources)}\n"
        content += f"- **Unique entities mentioned:** {len(people_counter)}\n"
        content += f"- **Unique tags used:** {len(tag_counter)}\n"
        content += f"- **Unique source IDs cited:** {len(source_counter)}\n\n"

        cert_dist = Counter(f.get('certainty', 0) for f in self.facts)
        content += "## Certainty Distribution\n\n| Score | Count | Bar |\n|---|---|---|\n"
        for score in range(11):
            count = cert_dist.get(score, 0)
            bar   = '█' * min(count, 50)
            content += f"| {score} | {count} | {bar} |\n"
        content += "\n"

        content += "## Most-Referenced Entities (top 20)\n\n| Entity | Count |\n|---|---|\n"
        for entity, count in people_counter.most_common(20):
            content += f"| {entity} | {count} |\n"
        content += "\n"

        content += "## Most-Used Tags (top 20)\n\n| Tag | Count |\n|---|---|\n"
        for tag, count in tag_counter.most_common(20):
            content += f"| {tag} | {count} |\n"
        content += "\n"

        content += "## Most-Cited Sources (top 20)\n\n| Source ID | Citations |\n|---|---|\n"
        for sid, count in source_counter.most_common(20):
            content += f"| `{sid}` | {count} |\n"

        self._write("stats.md", content)
        print(f"  stats: 1 file")

    def _write(self, relative_path: str, content: str):
        filepath = VIEWS_DIR / relative_path
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content)

    def print_stats(self):
        print(f"\n═══ Facts Database Stats ═══")
        print(f"  Facts:   {len(self.facts)}")
        print(f"  Media:   {len(self.media)}")
        print(f"  Sources: {len(self.sources)}")
        if self.facts:
            status_counts = Counter(f.get('status', '?') for f in self.facts)
            print(f"\n  By status:")
            for s, c in sorted(status_counts.items()):
                print(f"    {s}: {c}")
            cert_avg = sum(f.get('certainty', 0) for f in self.facts) / len(self.facts)
            print(f"\n  Avg certainty: {cert_avg:.1f}/10")
            years = {f.get('date', {}).get('value', '')[:4]
                     for f in self.facts
                     if f.get('date', {}).get('value', '')[:4].isdigit()}
            if years:
                print(f"  Year range: {min(years)}-{max(years)}")


import re

def main():
    if '--stats' in sys.argv:
        ViewGenerator().print_stats()
    else:
        ViewGenerator().generate_all()


if __name__ == '__main__':
    main()
