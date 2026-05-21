import logging
from app.agents.base import BaseAgent

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are an AI search analyst specialising in competitive intelligence for B2B SaaS companies.

Your task: generate realistic, commercially-relevant questions that potential customers type into
AI assistants (ChatGPT, Claude, Perplexity) when researching products in a given industry.

RULES:
1. Return ONLY valid JSON — absolutely no markdown, preamble, or explanation outside the JSON.
2. The JSON must be a single object: {"queries": [...]}
3. Every query object must have EXACTLY these fields:
   - "query_text"       : string  — the full natural-language question
   - "query_category"   : string  — one of: category_evaluation, comparison, feature_research,
                                    pricing, use_case, problem_solution, best_of
   - "commercial_intent": string  — one of: high, medium, low

QUALITY CRITERIA (queries that score full marks):
- Comparison/best-of queries ("X vs Y", "best tool for Z") → commercial_intent: high
- Direct competitor callouts → highly valuable
- Problem-solution queries where the product is the answer → medium
- Avoid generic informational queries with no buying signal
- Mix query lengths: 5-word queries and 15-word questions both occur naturally\
"""

_USER_TEMPLATE = """\
Generate {num_queries} AI search queries for this business profile.

Business name  : {name}
Domain         : {domain}
Industry       : {industry}
Description    : {description}
Competitors    : {competitors}

Include:
  • At least 4 direct comparisons against the listed competitors
  • At least 3 "best [category] tool" evaluator queries
  • At least 2 use-case queries specific to {industry}
  • At least 2 problem-solution queries where {name} is the natural answer
  • Mix of short and long-tail queries

Return this exact JSON (no other text):
{{
  "queries": [
    {{
      "query_text": "What is the best SEO content optimization tool in 2025?",
      "query_category": "category_evaluation",
      "commercial_intent": "high"
    }}
  ]
}}\
"""


class QueryDiscoveryAgent(BaseAgent):
    """
    Agent 1 — Query Discovery.

    Uses Claude to generate 10-20 realistic, commercially-relevant questions
    that potential customers ask AI assistants when evaluating tools in a
    competitive space. The prompt enforces a structured JSON schema and explicit
    quality criteria so output is consistently parseable.
    """

    def discover_queries(
        self,
        name: str,
        domain: str,
        industry: str,
        description: str,
        competitors: list[str],
        num_queries: int = 15,
    ) -> tuple[list[dict], int]:
        """
        Generate discovery queries for a business profile.
        Returns (validated query list, tokens_used).
        Malformed LLM output returns an empty list — never raises.
        """
        competitors_str = ", ".join(competitors) if competitors else "none specified"
        user_prompt = _USER_TEMPLATE.format(
            num_queries=num_queries,
            name=name,
            domain=domain,
            industry=industry,
            description=description or f"{name} — {industry}",
            competitors=competitors_str,
        )

        content, tokens = self._call_llm(_SYSTEM_PROMPT, user_prompt)
        parsed = self._parse_json_response(content, fallback={"queries": []})

        raw_queries = parsed.get("queries", []) if isinstance(parsed, dict) else []

        valid = []
        seen_texts: set[str] = set()
        for item in raw_queries:
            if not isinstance(item, dict):
                continue
            text = str(item.get("query_text", "")).strip()
            if len(text) < 10 or text.lower() in seen_texts:
                continue
            seen_texts.add(text.lower())
            valid.append(
                {
                    "query_text": text,
                    "query_category": item.get("query_category", "general"),
                    "commercial_intent": item.get("commercial_intent", "medium"),
                }
            )

        logger.info(
            "QueryDiscoveryAgent: %d valid queries from LLM output (%d tokens)",
            len(valid),
            tokens,
        )
        return valid, tokens
