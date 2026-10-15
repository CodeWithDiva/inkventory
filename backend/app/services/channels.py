"""Sales-channel profiles: content rules given to the AI plus compliance checks on the result.

Limits are sensible defaults based on each marketplace's public listing guidelines; marketplaces
change them from time to time, so review them for your categories and adjust here.
"""

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Channel:
    id: str
    name: str
    title_max: int = 70
    bullets_min: int = 3
    bullets_max: int = 6
    bullet_max_chars: int = 0  # 0 = no limit
    description_max_chars: int = 0
    banned_phrases: tuple[str, ...] = ()
    rules: tuple[str, ...] = field(default_factory=tuple)


PROMO_PHRASES = (
    "best seller", "bestseller", "free shipping", "on sale", "sale", "discount", "cheapest", "guarantee",
    "guaranteed", "100% satisfaction", "limited time", "buy now", "#1",
)

CHANNELS: dict[str, Channel] = {
    "generic": Channel("generic", "Generic / own website"),
    "shopify": Channel("shopify", "Shopify store", title_max=70,
                       rules=("Write for a branded online store; the description is shown as HTML paragraphs and a list.",)),
    "amazon": Channel(
        "amazon", "Amazon", title_max=200, bullets_min=5, bullets_max=5, bullet_max_chars=255,
        description_max_chars=2000, banned_phrases=PROMO_PHRASES,
        rules=(
            "Title: Brand + product name + key attributes (size, material, color, quantity). No promotional words, "
            "no ALL CAPS, no special characters like ! * $ ?.",
            "Exactly 5 bullet points, each starting with a short capitalised benefit, factual, no promotional claims.",
            "Description: plain text, no HTML, no contact details, no pricing, shipping or company information.",
            "Do not mention competitors, reviews, awards or guarantees.",
        ),
    ),
    "ebay": Channel(
        "ebay", "eBay", title_max=80, bullets_min=3, bullets_max=8, banned_phrases=("l@@k", "wow", "free shipping"),
        rules=(
            "Title: max 80 characters, lead with brand and the exact item; use the space for searchable details.",
            "No keyword spam and no promotional words in the title.",
        ),
    ),
    "daraz": Channel(
        "daraz", "Daraz", title_max=120, bullets_min=3, bullets_max=8, bullet_max_chars=200,
        banned_phrases=("whatsapp", "call now", "contact us", "cash on delivery only"),
        rules=(
            "Title: Brand + product name + key specification; clear and simple for mobile shoppers.",
            "Bullets are shown as 'Highlights': short, scannable, factual.",
            "No phone numbers, links or contact details anywhere.",
        ),
    ),
}


def get_channel(channel_id: str | None) -> Channel:
    return CHANNELS.get(channel_id or "generic", CHANNELS["generic"])


def channel_prompt(channel: Channel) -> list[str]:
    if channel.id == "generic":
        return []
    lines = [f"## Channel: {channel.name}", f"- title: max {channel.title_max} characters."]
    if channel.bullets_min == channel.bullets_max:
        lines.append(f"- bullet_points: exactly {channel.bullets_min}.")
    else:
        lines.append(f"- bullet_points: {channel.bullets_min}-{channel.bullets_max}.")
    if channel.bullet_max_chars:
        lines.append(f"- Each bullet max {channel.bullet_max_chars} characters.")
    if channel.description_max_chars:
        lines.append(f"- long_description max {channel.description_max_chars} characters.")
    if channel.banned_phrases:
        lines.append("- Never use: " + ", ".join(f'"{p}"' for p in channel.banned_phrases) + ".")
    lines += [f"- {rule}" for rule in channel.rules]
    return lines


def channel_checks(channel: Channel, *, title: str, bullet_points: list[str], long_description: str) -> list[dict]:
    """Compliance checks, in the same shape as SEO checks."""
    if channel.id == "generic":
        return []
    checks = [
        {"id": "ch_title", "label": f"{channel.name}: title ≤ {channel.title_max} chars",
         "passed": len(title) <= channel.title_max, "weight": 8, "detail": f"Title is {len(title)} characters"},
        {"id": "ch_bullets", "label": f"{channel.name}: {channel.bullets_min}-{channel.bullets_max} bullets",
         "passed": channel.bullets_min <= len(bullet_points) <= channel.bullets_max, "weight": 6,
         "detail": f"{len(bullet_points)} bullet points"},
    ]
    if channel.bullet_max_chars:
        too_long = [i + 1 for i, b in enumerate(bullet_points) if len(b) > channel.bullet_max_chars]
        checks.append({"id": "ch_bullet_len", "label": f"{channel.name}: bullets ≤ {channel.bullet_max_chars} chars",
                       "passed": not too_long, "weight": 4,
                       "detail": f"Too long: bullet {', '.join(map(str, too_long))}" if too_long else "All bullets fit"})
    if channel.description_max_chars:
        checks.append({"id": "ch_desc_len", "label": f"{channel.name}: description ≤ {channel.description_max_chars} chars",
                       "passed": len(long_description) <= channel.description_max_chars, "weight": 4,
                       "detail": f"Description is {len(long_description)} characters"})
    if channel.banned_phrases:
        text = " ".join([title, long_description, *bullet_points]).lower()
        found = [p for p in channel.banned_phrases if re.search(rf"(?<!\w){re.escape(p)}(?!\w)", text)]
        checks.append({"id": "ch_banned", "label": f"{channel.name}: no prohibited phrases",
                       "passed": not found, "weight": 8,
                       "detail": f"Found: {', '.join(found)}" if found else "None found"})
    return checks
