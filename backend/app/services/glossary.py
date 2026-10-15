"""Organization glossary: words to avoid (optionally auto-replaced) and terms to keep exactly."""

import re
from dataclasses import dataclass

from app.services.llm import GeneratedContent


@dataclass
class Term:
    term: str
    kind: str  # "avoid" | "keep"
    replacement: str | None = None


def prompt_lines(terms: list[Term]) -> list[str]:
    avoid = [t for t in terms if t.kind == "avoid"]
    keep = [t for t in terms if t.kind == "keep"]
    if not (avoid or keep):
        return []
    lines = ["## Terminology (company glossary)"]
    for t in avoid:
        lines.append(f'- Never use "{t.term}"' + (f'; say "{t.replacement}" instead.' if t.replacement else "."))
    if keep:
        lines.append("- Always write these exactly as given, never translate or alter them: "
                     + ", ".join(f'"{t.term}"' for t in keep) + ".")
    return lines


def _pattern(term: str) -> re.Pattern:
    return re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)


def apply(content: GeneratedContent, terms: list[Term]) -> tuple[GeneratedContent, list[str], list[str]]:
    """Auto-replace avoided terms that have a replacement. Returns (content, replaced, still_present)."""
    replaced: list[str] = []
    data = content.model_dump()
    for t in terms:
        if t.kind != "avoid" or not t.replacement:
            continue
        pattern = _pattern(t.term)
        hit = False
        for key, value in data.items():
            if isinstance(value, str):
                new, n = pattern.subn(t.replacement, value)
                data[key], hit = new, hit or n > 0
            elif isinstance(value, list):
                new_list = []
                for item in value:
                    new, n = pattern.subn(t.replacement, item)
                    new_list.append(new)
                    hit = hit or n > 0
                data[key] = new_list
        if hit:
            replaced.append(f"{t.term} → {t.replacement}")
    text = " ".join(str(v) if isinstance(v, str) else " ".join(v) for v in data.values())
    present = [t.term for t in terms if t.kind == "avoid" and not t.replacement and _pattern(t.term).search(text)]
    return GeneratedContent(**data), replaced, present
