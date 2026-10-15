"""Quality guards: possible hallucinated facts and near-duplicate descriptions."""

import re

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def _shingles(text: str, size: int = 3) -> set[tuple[str, ...]]:
    words = re.findall(r"\w+", text.lower())
    if len(words) < size:
        return {tuple(words)} if words else set()
    return {tuple(words[i : i + size]) for i in range(len(words) - size + 1)}


def similarity(a: str, b: str) -> float:
    """Jaccard similarity of word 3-gram shingles (0 = different, 1 = identical)."""
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def find_unsupported_numbers(generated_text: str, source_text: str) -> list[str]:
    """Numbers in the generated copy that do not appear anywhere in the product data.

    Invented numbers (sizes, capacities, warranty years, percentages) are the most common
    and most harmful kind of hallucination in product copy, so they are flagged for review.
    """
    source_numbers = {n.replace(",", ".") for n in _NUMBER.findall(source_text)}
    flagged: list[str] = []
    for number in _NUMBER.findall(generated_text):
        normalized = number.replace(",", ".")
        if normalized not in source_numbers and normalized not in flagged:
            flagged.append(normalized)
    return flagged


# Unicode ranges of the native script for languages not written in Latin letters.
SCRIPTS: dict[str, list[tuple[int, int]]] = {
    "ur": [(0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)],
    "ar": [(0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)],
    "hi": [(0x0900, 0x097F)],
    "ru": [(0x0400, 0x04FF)],
    "zh": [(0x4E00, 0x9FFF), (0x3400, 0x4DBF)],
    "ja": [(0x3040, 0x30FF), (0x4E00, 0x9FFF)],
    "ko": [(0xAC00, 0xD7AF), (0x1100, 0x11FF)],
}


def wrong_language(text: str, language: str) -> bool:
    """True when a non-Latin-script language was requested but most letters are not in that script."""
    ranges = SCRIPTS.get(language)
    if not ranges:
        return False
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    native = sum(1 for ch in letters if any(lo <= ord(ch) <= hi for lo, hi in ranges))
    return native / len(letters) < 0.5


def find_duplicates(text: str, others: list[tuple[str, str]], threshold: float) -> list[dict]:
    """Compare against (sku, text) pairs from the same catalog and language."""
    matches = []
    for sku, other_text in others:
        score = similarity(text, other_text)
        if score >= threshold:
            matches.append({"sku": sku, "similarity": round(score, 2)})
    return sorted(matches, key=lambda m: m["similarity"], reverse=True)[:5]


def find_cliches(text: str, language: str) -> list[str]:
    """Overused marketing phrases (English) that make copy sound generic or machine-written."""
    if language != "en":
        return []
    from app.services.prompts import CLICHES

    lower = text.lower()
    return [c for c in CLICHES if re.search(rf"\b{re.escape(c)}\b", lower)][:5]


# ---------- Unsupported claims ----------
# Materials and claims that models like to add. Flagged when absent from the product data.
CLAIM_TERMS: tuple[str, ...] = (
    "leather", "cotton", "silk", "wool", "linen", "cashmere", "polyester", "nylon", "denim", "velvet",
    "gold", "silver", "sterling", "platinum", "diamond", "pearl", "stainless steel", "aluminum", "aluminium",
    "titanium", "copper", "brass", "wood", "wooden", "bamboo", "glass", "ceramic", "porcelain", "marble", "plastic",
    "silicone", "rubber", "waterproof", "water-resistant", "water resistant", "hypoallergenic", "organic",
    "handmade", "hand-made", "handcrafted", "hand-crafted", "eco-friendly", "sustainable", "recycled", "vegan",
    "bpa-free", "bpa free", "dishwasher safe", "dishwasher-safe", "machine washable", "warranty", "guarantee",
    "certified", "award-winning", "clinically", "dermatologist", "non-toxic", "lifetime", "rechargeable",
    "wireless", "bluetooth", "noise cancelling", "noise-cancelling", "14k", "18k", "24k",
)
COLORS: tuple[str, ...] = (
    "black", "white", "red", "blue", "green", "yellow", "pink", "purple", "orange", "brown", "grey", "gray", "beige",
    "navy", "teal", "maroon", "ivory", "turquoise", "rose gold", "cream", "tan",
)


HEDGE_AFTER = re.compile(r"^[\s-]*(look|tone|toned|style|effect|finish|colou?red|colou?r|inspired|like)\b")
HEDGE_BEFORE = re.compile(r"(faux|imitation|vegan|appears?|appearing|looks?\s+like)\s*$")


def _always_hedged(lower: str, term: str) -> bool:
    """True when every mention of a material is phrased as an appearance ("gold-tone", "leather-look")."""
    for match in re.finditer(rf"\b{re.escape(term)}\b", lower):
        after, before = lower[match.end():match.end() + 14], lower[max(0, match.start() - 16):match.start()]
        if not (HEDGE_AFTER.match(after) or HEDGE_BEFORE.search(before)):
            return False
    return True


def find_unsupported_claims(generated_text: str, source_text: str, allow_visual: bool = False,
                            appearance_only: list[str] | None = None) -> list[str]:
    """Materials, colors and claims in the copy that the product data never mentions (English only).

    Colors are skipped when the writer saw the photos itself. Materials the photos only suggested
    (``appearance_only``) are accepted when phrased as an appearance, e.g. "gold-tone", "leather-look".
    """
    lower, source = generated_text.lower(), source_text.lower()
    suggested = " ".join(appearance_only or []).lower()
    terms = CLAIM_TERMS if allow_visual else CLAIM_TERMS + COLORS
    found: list[str] = []
    for term in terms:
        stem = term.rstrip("s")
        if re.search(rf"\b{re.escape(term)}\b", lower) and stem not in source and term.replace("-", " ") not in source:
            if term in suggested and _always_hedged(lower, term):
                continue
            found.append(term)
    return found[:6]


# ---------- Machine-written style ----------
WEAK_OPENERS = ("discover", "introducing", "experience", "elevate", "enhance", "unleash", "unlock", "meet",
                "step into", "embrace", "add a touch", "say hello", "upgrade your", "transform your", "looking for")


def ai_style_issues(text: str, language: str) -> list[str]:
    """Signs that copy reads machine-written rather than written by a person (English only)."""
    if language != "en" or not text.strip():
        return []
    from app.services.prompts import AI_TELLS

    issues: list[str] = []
    lower = text.lower()
    tells = [t for t in AI_TELLS if re.search(rf"\b{re.escape(t)}\b", lower)]
    if tells:
        issues.append(f"replace stock phrases ({', '.join(tells[:5])})")
    first = lower.lstrip()
    opener = next((o for o in WEAK_OPENERS if first.startswith(o)), None)
    if opener or first.startswith(("are you", "do you", "have you", "ever ")):
        issues.append(f'open with a concrete detail instead of "{(opener or first.split()[0]).capitalize()}..."')
    if text.count("—") > 1:
        issues.append("use commas or periods instead of em dashes")
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.split()) > 2]
    starts = [s.split()[0].lower().strip('"') for s in sentences]
    repeated = sorted({w for w in starts if starts.count(w) >= 3})
    if repeated:
        examples = [s[:60] for s in sentences if s.split()[0].lower().strip('"') in repeated][:3]
        issues.append(f"vary how sentences start (too many start with: {', '.join(repeated)}; rewrite openings such as "
                      + "; ".join(f'"{e}..."' for e in examples) + ")")
    if len(sentences) >= 5:
        lengths = [len(s.split()) for s in sentences]
        mean = sum(lengths) / len(lengths)
        spread = (sum((n - mean) ** 2 for n in lengths) / len(lengths)) ** 0.5
        if spread < 2.5:
            issues.append("mix short and long sentences; every sentence has a similar length "
                          f"({', '.join(str(n) for n in lengths[:8])} words): add a few short sentences of 4-8 words "
                          "and let one or two run longer")
    if text.count("?") >= 2:
        issues.append("drop the rhetorical questions")
    return issues
