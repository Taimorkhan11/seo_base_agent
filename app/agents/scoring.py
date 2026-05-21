import logging
from typing import Optional
from app.agents.base import BaseAgent

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are an AI visibility analyst. You simulate how a leading AI assistant (like ChatGPT or Claude)
would respond to a user query, then assess whether a specific business domain is mentioned.

RULES:
1. Return ONLY valid JSON — no markdown, no explanation outside the JSON.
2. Simulate the AI response as a 2-3 paragraph or bulleted-list answer that a real AI would give.
3. Be realistic: only mark domain_visible=true if the domain is genuinely well-known for this query.
   Do NOT always include the target domain — omission is realistic and expected.
4. visibility_position counts mention order (1 = first business mentioned).
5. visibility_context is the exact sentence/phrase that mentions the domain, or null.

Output schema (no other keys):
{
  "simulated_response"  : "string — the full simulated AI answer",
  "domain_visible"      : true | false,
  "visibility_position" : integer | null,
  "visibility_context"  : "string | null"
}\
"""

_USER_TEMPLATE = """\
Query: "{query_text}"

Target domain   : {domain}
Industry        : {industry}
Known competitors: {competitors}

Task:
  1. Write a realistic AI assistant response (2-3 paragraphs or a list) for the query above.
     Mention 3-5 relevant tools/services from the industry — draw on your knowledge of the space.
  2. Decide: does {domain} appear in that response?
  3. If yes, at what position is it first mentioned (1=first tool mentioned)?
  4. If yes, quote the exact sentence that mentions it.

Return the JSON schema described in your system prompt (no other text).\
"""


class VisibilityScoringAgent(BaseAgent):
    """
    Agent 2 — Visibility Scoring.

    Combines two data sources:
    • DataForSEO (real API)  — search volume and keyword competition index
    • Claude simulation      — whether the target domain appears in an AI-generated answer

    Gracefully degrades: if DataForSEO is unavailable the client returns heuristic
    estimates; if the LLM returns malformed JSON the scoring defaults to safe values.
    """

    def __init__(self, api_key: str, dataforseo_client=None):
        super().__init__(api_key)
        self.dataforseo_client = dataforseo_client

    def score_query(
        self,
        query_text: str,
        domain: str,
        industry: str,
        competitors: list[str],
        search_volume: Optional[int] = None,
        difficulty: Optional[int] = None,
    ) -> tuple[dict, int]:
        """
        Score a single query for domain visibility.
        Returns (result dict, tokens_used).
        Result keys: search_volume, competitive_difficulty, domain_visible,
                     visibility_position, visibility_context, simulated_response.
        """
        # Fetch real SEO metrics when not pre-supplied
        if search_volume is None or difficulty is None:
            seo = self._get_seo_data(query_text)
            search_volume = search_volume if search_volume is not None else seo.get("search_volume", 0)
            difficulty = difficulty if difficulty is not None else seo.get("difficulty", 50)

        competitors_str = ", ".join(competitors) if competitors else "none specified"
        user_prompt = _USER_TEMPLATE.format(
            query_text=query_text,
            domain=domain,
            industry=industry,
            competitors=competitors_str,
        )

        content, tokens = self._call_llm(_SYSTEM_PROMPT, user_prompt)
        parsed = self._parse_json_response(content, fallback={})
        if not isinstance(parsed, dict):
            parsed = {}

        # Coerce domain_visible to bool regardless of LLM formatting
        raw_visible = parsed.get("domain_visible", False)
        if isinstance(raw_visible, str):
            domain_visible = raw_visible.strip().lower() == "true"
        else:
            domain_visible = bool(raw_visible)

        visibility_position = parsed.get("visibility_position")
        if visibility_position is not None:
            try:
                visibility_position = int(visibility_position)
            except (ValueError, TypeError):
                visibility_position = None

        result = {
            "search_volume": max(int(search_volume), 0),
            "competitive_difficulty": max(0, min(int(difficulty), 100)),
            "domain_visible": domain_visible,
            "visibility_position": visibility_position if domain_visible else None,
            "visibility_context": parsed.get("visibility_context") if domain_visible else None,
            "simulated_response": parsed.get("simulated_response", ""),
        }

        logger.info(
            "VisibilityScoringAgent: query='%.50s' visible=%s vol=%d diff=%d tokens=%d",
            query_text,
            domain_visible,
            result["search_volume"],
            result["competitive_difficulty"],
            tokens,
        )
        return result, tokens

    def _get_seo_data(self, query_text: str) -> dict:
        """Fetch from DataForSEO; fall back to heuristics if unavailable."""
        if self.dataforseo_client:
            try:
                return self.dataforseo_client.get_keyword_data(query_text)
            except Exception as exc:
                logger.warning("DataForSEO failed for '%s': %s — using fallback", query_text, exc)
        return {}
