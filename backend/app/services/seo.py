"""Rule-based SEO scoring (0-100) for generated content.

Each check has a weight; the score is the weighted share of passed checks. The report is
shown to reviewers so they can see exactly why a description scored the way it did.
"""

import re
from dataclasses import asdict, dataclass

from app.services.prompts import LENGTHS

# Languages written without spaces between words; word-count based checks are skipped.
NO_SPACE_LANGUAGES = {"zh", "ja", "ko"}


@dataclass
class Check:
    id: str
    label: str
    passed: bool
    weight: int
    detail: str


def _contains(text: str, phrase: str) -> bool:
    return phrase.lower() in text.lower()


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def _count_phrase(text: str, phrase: str) -> int:
    return len(re.findall(re.escape(phrase.lower()), text.lower()))


def _syllables(word: str) -> int:
    word = word.lower()
    groups = re.findall(r"[aeiouy]+", word)
    count = len(groups) - (1 if word.endswith("e") and len(groups) > 1 and not word.endswith("le") else 0)
    return max(1, count)


def flesch_reading_ease(text: str) -> float:
    """Flesch Reading Ease for English (higher = easier; 60-70 is plain English)."""
    words = re.findall(r"[A-Za-z']+", text)
    sentences = max(1, len([s for s in re.split(r"[.!?]+", text) if s.strip()]))
    if not words:
        return 100.0
    syllables = sum(_syllables(w) for w in words)
    return 206.835 - 1.015 * (len(words) / sentences) - 84.6 * (syllables / len(words))


CALL_TO_ACTION = re.compile(r"\b(shop|buy|order|get yours|browse|find|explore|add it|grab|choose|pick up|see)\b", re.I)


def score_content(
    *,
    title: str,
    short_description: str,
    long_description: str,
    bullet_points: list[str],
    meta_title: str,
    meta_description: str,
    keywords: list[str],
    language: str = "en",
    length: str = "medium",
    channel: str = "generic",
) -> tuple[int, dict]:
    from app.services.channels import channel_checks, get_channel

    ch = get_channel(channel)
    checks: list[Check] = []
    full_text = " ".join([title, short_description, long_description, *bullet_points])
    primary = keywords[0] if keywords else None
    words = _words(long_description)
    spaced = language not in NO_SPACE_LANGUAGES

    if primary:
        first_sentence = re.split(r"(?<=[.!?。])\s+", long_description.strip(), maxsplit=1)[0]
        checks += [
            Check("kw_title", "Primary keyword in title", _contains(title, primary), 15,
                  f'"{primary}" should appear in the title'),
            Check("kw_meta", "Primary keyword in meta description", _contains(meta_description, primary), 12,
                  f'"{primary}" should appear in the meta description'),
            Check("kw_intro", "Primary keyword in first sentence", _contains(first_sentence, primary), 10,
                  "Search engines weigh the opening sentence heavily"),
            Check("kw_meta_title", "Primary keyword in meta title", _contains(meta_title, primary), 8,
                  f'"{primary}" should appear in the search result title'),
        ]
        position = title.lower().find(primary.lower())
        if position >= 0:
            checks.append(Check("kw_title_front", "Keyword near the start of the title", position <= len(title) * 0.5, 4,
                                "Words at the start of a title carry the most weight in search results"))
        covered = [k for k in keywords if _contains(full_text, k)]
        checks.append(Check(
            "kw_coverage", "Keyword coverage", len(covered) >= max(1, round(len(keywords) * 0.6)), 10,
            f"{len(covered)}/{len(keywords)} keywords used: {', '.join(covered) or 'none'}",
        ))
        if spaced and words:
            occurrences = _count_phrase(long_description, primary)
            density = occurrences * len(primary.split()) / len(words) * 100
            checks.append(Check(
                "kw_density", "Keyword density 0.5-3%", 0.5 <= density <= 3.0, 8,
                f"Primary keyword density is {density:.1f}%",
            ))

    checks += [
        Check("title_len", f"Title length 20-{ch.title_max} chars", 20 <= len(title) <= ch.title_max, 8,
              f"Title is {len(title)} characters"),
        Check("meta_title_len", "Meta title 30-60 chars", 30 <= len(meta_title) <= 60, 8,
              f"Meta title is {len(meta_title)} characters"),
        Check("meta_desc_len", "Meta description 120-160 chars", 120 <= len(meta_description) <= 160, 10,
              f"Meta description is {len(meta_description)} characters"),
        Check("bullets", f"{ch.bullets_min}-{ch.bullets_max} bullet points",
              ch.bullets_min <= len(bullet_points) <= ch.bullets_max, 5, f"{len(bullet_points)} bullet points"),
    ]

    if spaced:
        min_words, max_words = LENGTHS.get(length, LENGTHS["medium"])
        # Allow some slack around the requested range.
        ok = min_words * 0.8 <= len(words) <= max_words * 1.25
        checks.append(Check("length", f"Description length ~{min_words}-{max_words} words", ok, 8,
                            f"Description has {len(words)} words"))
        sentences = [s for s in re.split(r"[.!?]+", long_description) if s.strip()]
        avg = len(words) / max(1, len(sentences))
        checks.append(Check("readability", "Readable sentences (avg <= 22 words)", avg <= 22, 6,
                            f"Average sentence length is {avg:.0f} words"))
        if language == "en":
            ease = flesch_reading_ease(long_description)
            checks.append(Check("reading_ease", "Easy to read (Flesch score 50+)", ease >= 50, 6,
                                f"Reading ease is {ease:.0f}; shorter words and sentences raise it"))
            checks.append(Check("meta_cta", "Meta description invites a click", bool(CALL_TO_ACTION.search(meta_description)), 4,
                                'End with an action such as "Shop now" or "Order yours"'))

    checks += [Check(**c) for c in channel_checks(ch, title=title, bullet_points=bullet_points,
                                                   long_description=long_description)
               if c["id"] not in ("ch_title", "ch_bullets")]  # those are covered above with channel limits

    total = sum(c.weight for c in checks)
    earned = sum(c.weight for c in checks if c.passed)
    score = round(earned / total * 100) if total else 0
    return score, {"checks": [asdict(c) for c in checks]}
