"""
DataForSEO Keywords Data client.

Uses the Google Ads Search Volume Live endpoint to fetch real monthly search
volume and competition index for a keyword.

Graceful degradation: if credentials are missing or the API call fails, the
_fallback_estimate() method returns heuristic values based on query length and
commercial keyword signals so the pipeline always has something to work with.
"""

import base64
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.dataforseo.com/v3"
_TIMEOUT = 15  # seconds


class DataForSEOClient:
    """Thin wrapper around the DataForSEO Keywords Data API."""

    def __init__(self, login: str, password: str):
        creds = base64.b64encode(f"{login}:{password}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/json",
        }

    def get_keyword_data(self, keyword: str, location_code: int = 2840) -> dict:
        """
        Fetch search volume and competition index for *keyword*.
        location_code 2840 = United States (Google Ads).

        Returns dict with keys:
          search_volume (int)  — avg monthly searches
          difficulty    (int)  — competition index 0-100 (mapped from 0.0-1.0)
        """
        payload = [
            {
                "keywords": [keyword],
                "location_code": location_code,
                "language_code": "en",
            }
        ]

        try:
            resp = requests.post(
                f"{_BASE_URL}/keywords_data/google_ads/search_volume/live",
                headers=self.headers,
                json=payload,
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            logger.warning("DataForSEO request error for '%s': %s", keyword, exc)
            return self._fallback_estimate(keyword)

        try:
            task = data["tasks"][0]
            if task.get("status_code") != 20000:
                logger.warning(
                    "DataForSEO non-success for '%s': %s",
                    keyword,
                    task.get("status_message", "unknown"),
                )
                return self._fallback_estimate(keyword)

            result = task["result"][0]
            volume = int(result.get("search_volume") or 0)
            # competition is 0.0–1.0 in DataForSEO; map to 0-100
            competition_raw = result.get("competition_index") or result.get("competition") or 0.5
            difficulty = int(float(competition_raw) * 100) if float(competition_raw) <= 1.0 else int(competition_raw)

            return {"search_volume": volume, "difficulty": min(max(difficulty, 0), 100)}

        except (KeyError, IndexError, ValueError, TypeError) as exc:
            logger.warning("DataForSEO parse error for '%s': %s", keyword, exc)
            return self._fallback_estimate(keyword)

    @staticmethod
    def _fallback_estimate(keyword: str) -> dict:
        """
        Heuristic estimates when DataForSEO is unavailable.
        Not accurate — used only when the real API cannot be reached.
        """
        words = keyword.lower().split()
        word_count = len(words)

        # Longer queries → lower volume (long-tail effect)
        if word_count <= 3:
            base_volume = 2200
        elif word_count <= 5:
            base_volume = 900
        elif word_count <= 8:
            base_volume = 350
        else:
            base_volume = 120

        commercial = {"best", "vs", "top", "review", "price", "compare", "alternative"}
        if any(w in commercial for w in words):
            base_volume = int(base_volume * 1.5)

        if any(w in {"best", "top", "vs", "compare", "alternative"} for w in words):
            difficulty = 65
        elif words[0] in {"how", "what", "why", "when", "where"}:
            difficulty = 35
        else:
            difficulty = 50

        return {"search_volume": base_volume, "difficulty": difficulty}
