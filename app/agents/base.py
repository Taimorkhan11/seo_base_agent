import json
import logging
import re
from typing import Any, Optional

import anthropic

logger = logging.getLogger(__name__)


class BaseAgent:
    """
    Shared base for all AI agents.
    Handles LLM calls, robust JSON parsing, and token tracking.
    All agents use Claude claude-sonnet-4-6 — see README for model selection rationale.
    """

    MODEL = "claude-sonnet-4-6"
    MAX_TOKENS = 4096

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._client: Optional[anthropic.Anthropic] = None
        self.total_tokens_used = 0

    @property
    def client(self) -> anthropic.Anthropic:
        """Lazy-initialize the Anthropic client — skipped entirely in mocked tests."""
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: Optional[int] = None,
    ) -> tuple[str, int]:
        """Call the LLM and return (response_text, tokens_used)."""
        response = self.client.messages.create(
            model=self.MODEL,
            max_tokens=max_tokens or self.MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        content = response.content[0].text
        tokens = response.usage.input_tokens + response.usage.output_tokens
        self.total_tokens_used += tokens
        return content, tokens

    def _parse_json_response(self, content: str, fallback: Any = None) -> Any:
        """
        Robustly parse JSON from LLM output.
        Handles: raw JSON, markdown code fences, leading/trailing prose.
        Returns fallback if all attempts fail — never raises.
        """
        # Attempt 1: direct parse
        try:
            return json.loads(content.strip())
        except json.JSONDecodeError:
            pass

        # Attempt 2: strip markdown code fences
        cleaned = re.sub(r"```(?:json)?\s*", "", content)
        cleaned = re.sub(r"```\s*", "", cleaned)
        cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Attempt 3: extract first JSON object or array
        for pattern in [r"\{[\s\S]*\}", r"\[[\s\S]*\]"]:
            match = re.search(pattern, cleaned)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass

        logger.warning(
            "%s: failed to parse JSON from LLM output (first 300 chars): %s",
            self.__class__.__name__,
            content[:300],
        )
        return fallback
