"""
Opportunity Score Formula
=========================

opportunity_score = 0.35 * volume_score
                  + 0.25 * ease_score
                  + 0.30 * visibility_gap_score
                  + 0.10 * intent_score

Component definitions
---------------------
volume_score         = log10(max(volume, 1)) / log10(100_000)   [0.0 – 1.0, capped]
                       Logarithmic: 100 vol → 0.40, 1 000 → 0.60, 10 000 → 0.80, 100 000 → 1.0
                       Log scale chosen because the SEO value of 10 000 vol vs 100 000 vol
                       is not 10× — it is roughly 1 increment of effort.

ease_score           = 1.0 − (difficulty / 100)                 [0.0 – 1.0]
                       Linear inverse of competitive difficulty.

visibility_gap_score = 1.0  if domain_visible is False  (not appearing → maximum opportunity)
                     = 0.5  if domain_visible is None   (unscored / unknown)
                     = 0.1  if domain_visible is True   (already appearing, low urgency)

intent_score         = 1.0  for high-commercial-intent queries  (vs, best, compare, review…)
                     = 0.6  for medium-intent queries           (how to, guide, features…)
                     = 0.2  for low-intent / informational      (what is, overview…)

Weight rationale
----------------
• visibility_gap (0.30) + volume (0.35) carry the most weight because a high-volume gap is
  the core definition of "opportunity" in AI visibility.
• ease (0.25) moderates by capture-ability — even a huge gap matters less if it's unwinnable.
• intent (0.10) is a tie-breaker: commercial queries justify investment over informational ones.
"""

import math
from typing import Optional

_COMMERCIAL_HIGH = frozenset(
    [
        "best", "top", "vs", "versus", "compare", "comparison", "alternative", "alternatives",
        "review", "reviews", "pricing", "price", "cost", "buy", "purchase", "affordable",
        "better", "which is", "should i use", "recommend", "recommended",
    ]
)
_COMMERCIAL_MEDIUM = frozenset(
    ["how to", "guide", "tutorial", "features", "pros and cons", "worth it", "use"]
)

_WEIGHTS = {"volume": 0.35, "ease": 0.25, "gap": 0.30, "intent": 0.10}


def calculate_volume_score(search_volume: int) -> float:
    if search_volume <= 0:
        return 0.0
    return min(math.log10(max(search_volume, 1)) / math.log10(100_000), 1.0)


def calculate_ease_score(competitive_difficulty: int) -> float:
    return 1.0 - min(max(competitive_difficulty, 0), 100) / 100.0


def calculate_visibility_gap_score(domain_visible: Optional[bool]) -> float:
    if domain_visible is None:
        return 0.5
    return 0.1 if domain_visible else 1.0


def calculate_intent_score(query_text: str) -> float:
    q = query_text.lower()
    if any(kw in q for kw in _COMMERCIAL_HIGH):
        return 1.0
    if any(kw in q for kw in _COMMERCIAL_MEDIUM):
        return 0.6
    return 0.2


def calculate_opportunity_score(
    search_volume: int,
    competitive_difficulty: int,
    domain_visible: Optional[bool],
    query_text: str,
) -> tuple[float, float]:
    """
    Compute the opportunity score and commercial intent score for a query.

    Returns
    -------
    (opportunity_score, commercial_intent_score)  both in [0.0, 1.0]
    """
    volume_score = calculate_volume_score(search_volume)
    ease_score = calculate_ease_score(competitive_difficulty)
    gap_score = calculate_visibility_gap_score(domain_visible)
    intent_score = calculate_intent_score(query_text)

    opportunity = (
        _WEIGHTS["volume"] * volume_score
        + _WEIGHTS["ease"] * ease_score
        + _WEIGHTS["gap"] * gap_score
        + _WEIGHTS["intent"] * intent_score
    )

    return round(min(opportunity, 1.0), 4), round(intent_score, 4)
