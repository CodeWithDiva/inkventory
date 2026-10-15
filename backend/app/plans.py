"""Subscription plans. Prices are display values; the payment provider charges the real amount."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    price_label: str
    monthly_generations: int
    max_users: int
    # Engine kinds the plan may use with the platform's own keys: "free", "local", "cloud", "demo".
    engine_kinds: tuple[str, ...]
    features: tuple[str, ...]


PLANS: dict[str, Plan] = {
    "free": Plan(
        id="free",
        name="Free",
        price_label="$0",
        monthly_generations=100,
        max_users=2,
        engine_kinds=("free", "local", "demo"),
        features=("100 descriptions / month", "2 team members", "Free AI (Gemini / Groq)",
                  "Claude with your own key", "CSV export"),
    ),
    "pro": Plan(
        id="pro",
        name="Pro",
        price_label="$29 / month",
        monthly_generations=2_000,
        max_users=5,
        engine_kinds=("free", "local", "demo", "cloud"),
        features=("2,000 descriptions / month", "5 team members", "Free AI + Claude with your own key",
                  "Shopify & WooCommerce push & sync"),
    ),
    "business": Plan(
        id="business",
        name="Business",
        price_label="$99 / month",
        monthly_generations=10_000,
        max_users=25,
        engine_kinds=("free", "local", "demo", "cloud"),
        features=("10,000 descriptions / month", "25 team members", "Free AI + Claude with your own key",
                  "Priority support"),
    ),
}

DEFAULT_PLAN = "free"


def get_plan(plan_id: str | None) -> Plan:
    return PLANS.get(plan_id or DEFAULT_PLAN, PLANS[DEFAULT_PLAN])
