"""
What a cloud request costs - for the cost counter in the app and the
monthly budget (CLOUD_MONTHLY_BUDGET_USD).

Prices: OpenAI API pricing page, standard tier, short context,
USD per 1 million tokens - checked 2026-10-08
(https://developers.openai.com/api/docs/pricing). Prices change: for a
model that isn't listed (or to correct a value) set
CLOUD_PRICE_PER_1M="input,cached_input,output" in .env.

Local requests cost nothing and are only counted in tokens.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from app.config import Settings

PRICES_CHECKED = "2026-10-08"


@dataclass(frozen=True)
class Price:
    input: float
    cached_input: float
    output: float


PRICES_PER_1M: Dict[str, Price] = {
    "gpt-6-luna": Price(0.10, 0.01, 0.50),
    "gpt-6.1-sol": Price(2.00, 0.10, 10.00),
    "gpt-6-astra": Price(10.00, 10.00, 50.00),  # cached price not published - counted as normal input
    "gpt-5.6-luna": Price(0.20, 0.02, 1.20),
    "gpt-5.6-terra": Price(2.00, 0.20, 12.00),
    "gpt-5.6-sol": Price(4.00, 0.40, 20.00),
}


def price_for(model: str, settings: Optional[Settings] = None) -> Optional[Price]:
    override = (settings.CLOUD_PRICE_PER_1M if settings else "").strip()
    if override:
        try:
            parts = [float(x) for x in override.split(",")]
            if len(parts) == 3:
                return Price(*parts)
        except ValueError:
            pass
    return PRICES_PER_1M.get(model)


def cost_usd(model: str, prompt_tokens: int, cached_tokens: int, completion_tokens: int, settings: Optional[Settings] = None) -> Optional[float]:
    """USD for one request, or None if the model's price is unknown."""
    price = price_for(model, settings)
    if price is None:
        return None
    cached = min(max(cached_tokens, 0), max(prompt_tokens, 0))
    fresh = max(prompt_tokens, 0) - cached
    return (fresh * price.input + cached * price.cached_input + max(completion_tokens, 0) * price.output) / 1_000_000
