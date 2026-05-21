import logging
from app.agents.base import BaseAgent

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a content strategist specialising in Answer Engine Optimisation (AEO) — helping
businesses appear in AI-generated answers (ChatGPT, Claude, Perplexity).

Your task: given a set of queries where a target domain is NOT appearing, generate specific,
actionable content recommendations that would close each visibility gap.

RULES:
1. Return ONLY valid JSON — no markdown, no prose outside the JSON object.
2. Output schema: {"recommendations": [...]}
3. Every recommendation must have EXACTLY these fields:
   - "target_query_text"    : string  — exact query text from the input list
   - "content_type"         : string  — one of: blog_post, landing_page, faq,
                                         comparison_guide, case_study, resource_hub
   - "title"                : string  — a specific, publishable content title (not generic)
   - "rationale"            : string  — why THIS content will help the domain appear for THIS query
   - "target_keywords"      : array   — 3-8 exact keyword strings to optimise for
   - "priority"             : string  — one of: high, medium, low
   - "word_count_suggestion": integer — recommended article length
   - "key_sections"         : array   — 3-5 section headings for the content piece

QUALITY CRITERIA:
- Title must be specific enough to publish as-is (not "A Guide to X")
- Rationale must explain the specific gap, not give generic SEO advice
- Keywords must be long-tail and query-aligned, not single generic words
- Priority = high only for high-volume + high-intent + not-visible queries\
"""

_USER_TEMPLATE = """\
Business      : {name}
Domain        : {domain}
Industry      : {industry}
Description   : {description}

Queries where {domain} is NOT appearing in AI answers (sorted by opportunity, highest first):
{queries_section}

Generate 1-2 content recommendations per query (prioritise the top queries if list is long).
Each recommendation must directly address why {domain} is missing and what content would fix it.

Return the JSON schema from your system prompt (no other text).\
"""


class ContentRecommendationAgent(BaseAgent):
    """
    Agent 3 — Content Recommendations.

    Given the highest-opportunity queries where the target domain is absent,
    generates specific, actionable content briefs that would improve AI visibility.
    The prompt enforces a tight output schema and publication-ready specificity.
    """

    def generate_recommendations(
        self,
        name: str,
        domain: str,
        industry: str,
        description: str,
        top_opportunity_queries: list[dict],
    ) -> tuple[list[dict], int]:
        """
        Generate content recommendations for opportunity queries.
        Returns (validated recommendation list, tokens_used).
        Returns ([], 0) immediately when no queries are provided.
        """
        if not top_opportunity_queries:
            logger.warning("ContentRecommendationAgent: called with no opportunity queries")
            return [], 0

        queries_lines = []
        for i, q in enumerate(top_opportunity_queries, 1):
            vol = q.get("estimated_search_volume", "unknown")
            score = q.get("opportunity_score")
            diff = q.get("competitive_difficulty", "unknown")
            score_str = f"{score:.2f}" if score is not None else "n/a"
            queries_lines.append(
                f'{i}. "{q["query_text"]}" '
                f"(volume: {vol}, opportunity: {score_str}, difficulty: {diff})"
            )

        user_prompt = _USER_TEMPLATE.format(
            name=name,
            domain=domain,
            industry=industry,
            description=description or f"{name} — {industry}",
            queries_section="\n".join(queries_lines),
        )

        content, tokens = self._call_llm(_SYSTEM_PROMPT, user_prompt, max_tokens=6000)
        parsed = self._parse_json_response(content, fallback={"recommendations": []})

        raw_recs = []
        if isinstance(parsed, dict):
            raw_recs = parsed.get("recommendations", [])
        elif isinstance(parsed, list):
            raw_recs = parsed

        valid = []
        for rec in raw_recs:
            if not isinstance(rec, dict):
                continue
            title = str(rec.get("title", "")).strip()
            rationale = str(rec.get("rationale", "")).strip()
            if not title or not rationale:
                continue

            keywords = rec.get("target_keywords", [])
            if not isinstance(keywords, list):
                keywords = []

            priority = rec.get("priority", "medium")
            if priority not in ("high", "medium", "low"):
                priority = "medium"

            content_type = rec.get("content_type", "blog_post")
            valid_types = {
                "blog_post", "landing_page", "faq",
                "comparison_guide", "case_study", "resource_hub",
            }
            if content_type not in valid_types:
                content_type = "blog_post"

            valid.append(
                {
                    "target_query_text": str(rec.get("target_query_text", "")).strip(),
                    "content_type": content_type,
                    "title": title,
                    "rationale": rationale,
                    "target_keywords": [str(k).strip() for k in keywords[:10] if str(k).strip()],
                    "priority": priority,
                    "word_count_suggestion": rec.get("word_count_suggestion"),
                    "key_sections": rec.get("key_sections", []),
                }
            )

        logger.info(
            "ContentRecommendationAgent: %d recommendations generated (%d tokens)",
            len(valid),
            tokens,
        )
        return valid, tokens
