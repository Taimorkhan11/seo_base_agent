"""
Unit tests for AI agent logic using mocked LLM responses.

Coverage:
  - JSON parsing: valid JSON, markdown-fenced JSON, completely broken output
  - QueryDiscoveryAgent: happy path, deduplication, short-text filtering
  - VisibilityScoringAgent: visible / not-visible, malformed fallback, bool coercion
  - ContentRecommendationAgent: happy path, empty-query short-circuit, priority normalisation
  - Opportunity score formula: edge cases, range guarantees, intent detection
"""

import json
import unittest
from unittest.mock import patch

from app.agents.discovery import QueryDiscoveryAgent
from app.agents.scoring import VisibilityScoringAgent
from app.agents.recommendation import ContentRecommendationAgent
from app.utils.scoring import (
    calculate_opportunity_score,
    calculate_intent_score,
    calculate_volume_score,
    calculate_ease_score,
    calculate_visibility_gap_score,
)

# ── Fixture payloads ──────────────────────────────────────────────────────────

_DISCOVERY_PAYLOAD = json.dumps(
    {
        "queries": [
            {
                "query_text": "What is the best SEO content optimization tool in 2025?",
                "query_category": "category_evaluation",
                "commercial_intent": "high",
            },
            {
                "query_text": "Surfer SEO vs Clearscope — which is better for content teams?",
                "query_category": "comparison",
                "commercial_intent": "high",
            },
            {
                "query_text": "How to write SEO-optimized content with AI assistance",
                "query_category": "how_to",
                "commercial_intent": "medium",
            },
        ]
    }
)

_SCORING_NOT_VISIBLE = json.dumps(
    {
        "simulated_response": "For SEO content optimisation tools, Clearscope and MarketMuse are popular choices...",
        "domain_visible": False,
        "visibility_position": None,
        "visibility_context": None,
    }
)

_SCORING_VISIBLE = json.dumps(
    {
        "simulated_response": "Surfer SEO (surferseo.com) is widely recommended for content optimisation...",
        "domain_visible": True,
        "visibility_position": 1,
        "visibility_context": "Surfer SEO (surferseo.com) is widely recommended for content optimisation",
    }
)

_RECOMMENDATION_PAYLOAD = json.dumps(
    {
        "recommendations": [
            {
                "target_query_text": "What is the best SEO content optimization tool in 2025?",
                "content_type": "comparison_guide",
                "title": "Best SEO Content Optimization Tools in 2025: Surfer SEO vs 5 Alternatives",
                "rationale": "This high-intent query returns results dominated by MarketMuse and Clearscope. A comprehensive comparison that features Surfer SEO prominently with real benchmarks will give AI assistants specific evidence to cite.",
                "target_keywords": [
                    "best seo content tool",
                    "content optimization software",
                    "surfer seo review 2025",
                ],
                "priority": "high",
                "word_count_suggestion": 3200,
                "key_sections": [
                    "Evaluation criteria",
                    "Tool-by-tool breakdown",
                    "Pricing comparison",
                    "Verdict",
                ],
            }
        ]
    }
)


# ── QueryDiscoveryAgent tests ─────────────────────────────────────────────────

class TestQueryDiscoveryAgent(unittest.TestCase):
    def setUp(self):
        self.agent = QueryDiscoveryAgent(api_key="test-key-not-used")

    @patch.object(QueryDiscoveryAgent, "_call_llm")
    def test_happy_path(self, mock_llm):
        mock_llm.return_value = (_DISCOVERY_PAYLOAD, 500)

        queries, tokens = self.agent.discover_queries(
            name="Surfer SEO",
            domain="surferseo.com",
            industry="SEO Software",
            description="AI-powered content optimisation tool",
            competitors=["clearscope.io", "marketmuse.com"],
        )

        self.assertEqual(len(queries), 3)
        self.assertEqual(tokens, 500)
        self.assertEqual(queries[0]["commercial_intent"], "high")
        self.assertEqual(queries[1]["query_category"], "comparison")

    @patch.object(QueryDiscoveryAgent, "_call_llm")
    def test_malformed_json_returns_empty_list(self, mock_llm):
        mock_llm.return_value = ("This is definitely not JSON {{{{", 100)

        queries, _ = self.agent.discover_queries(
            name="Test", domain="test.com", industry="Tech", description="", competitors=[]
        )
        self.assertEqual(queries, [])

    @patch.object(QueryDiscoveryAgent, "_call_llm")
    def test_markdown_fenced_json_parsed_correctly(self, mock_llm):
        fenced = f"```json\n{_DISCOVERY_PAYLOAD}\n```"
        mock_llm.return_value = (fenced, 500)

        queries, _ = self.agent.discover_queries(
            name="Test", domain="test.com", industry="Tech", description="", competitors=[]
        )
        self.assertEqual(len(queries), 3)

    @patch.object(QueryDiscoveryAgent, "_call_llm")
    def test_short_query_texts_filtered_out(self, mock_llm):
        payload = json.dumps(
            {
                "queries": [
                    {"query_text": "Hi", "query_category": "test", "commercial_intent": "high"},
                    {"query_text": "A valid long enough query text here", "query_category": "test", "commercial_intent": "medium"},
                ]
            }
        )
        mock_llm.return_value = (payload, 200)

        queries, _ = self.agent.discover_queries(
            name="Test", domain="test.com", industry="Tech", description="", competitors=[]
        )
        self.assertEqual(len(queries), 1)

    @patch.object(QueryDiscoveryAgent, "_call_llm")
    def test_duplicate_query_texts_deduplicated(self, mock_llm):
        payload = json.dumps(
            {
                "queries": [
                    {"query_text": "Best SEO tool for content teams", "query_category": "test", "commercial_intent": "high"},
                    {"query_text": "Best SEO tool for content teams", "query_category": "test", "commercial_intent": "high"},
                ]
            }
        )
        mock_llm.return_value = (payload, 300)

        queries, _ = self.agent.discover_queries(
            name="Test", domain="test.com", industry="Tech", description="", competitors=[]
        )
        self.assertEqual(len(queries), 1)


# ── VisibilityScoringAgent tests ──────────────────────────────────────────────

class TestVisibilityScoringAgent(unittest.TestCase):
    def setUp(self):
        self.agent = VisibilityScoringAgent(api_key="test-key-not-used", dataforseo_client=None)

    @patch.object(VisibilityScoringAgent, "_call_llm")
    def test_not_visible_query(self, mock_llm):
        mock_llm.return_value = (_SCORING_NOT_VISIBLE, 400)

        result, tokens = self.agent.score_query(
            query_text="Best SEO content tool?",
            domain="surferseo.com",
            industry="SEO Software",
            competitors=["clearscope.io"],
            search_volume=1200,
            difficulty=62,
        )

        self.assertFalse(result["domain_visible"])
        self.assertIsNone(result["visibility_position"])
        self.assertEqual(result["search_volume"], 1200)
        self.assertEqual(result["competitive_difficulty"], 62)
        self.assertEqual(tokens, 400)

    @patch.object(VisibilityScoringAgent, "_call_llm")
    def test_visible_query(self, mock_llm):
        mock_llm.return_value = (_SCORING_VISIBLE, 450)

        result, _ = self.agent.score_query(
            query_text="Best SEO content tool?",
            domain="surferseo.com",
            industry="SEO Software",
            competitors=[],
            search_volume=5000,
            difficulty=70,
        )

        self.assertTrue(result["domain_visible"])
        self.assertEqual(result["visibility_position"], 1)
        self.assertIn("surferseo.com", result["visibility_context"])

    @patch.object(VisibilityScoringAgent, "_call_llm")
    def test_malformed_json_uses_safe_defaults(self, mock_llm):
        mock_llm.return_value = ("COMPLETELY BROKEN {{{ OUTPUT", 100)

        result, _ = self.agent.score_query(
            query_text="Test query",
            domain="test.com",
            industry="Tech",
            competitors=[],
            search_volume=500,
            difficulty=40,
        )

        self.assertIsInstance(result["domain_visible"], bool)
        self.assertFalse(result["domain_visible"])  # defaults to False

    @patch.object(VisibilityScoringAgent, "_call_llm")
    def test_string_true_coerced_to_bool(self, mock_llm):
        """LLM sometimes returns domain_visible as the string "true"."""
        payload = json.dumps(
            {
                "simulated_response": "...",
                "domain_visible": "true",
                "visibility_position": 2,
                "visibility_context": "mention",
            }
        )
        mock_llm.return_value = (payload, 300)

        result, _ = self.agent.score_query(
            query_text="Test?",
            domain="test.com",
            industry="Tech",
            competitors=[],
            search_volume=100,
            difficulty=50,
        )
        self.assertIs(result["domain_visible"], True)

    @patch.object(VisibilityScoringAgent, "_call_llm")
    def test_difficulty_clamped_to_100(self, mock_llm):
        mock_llm.return_value = (_SCORING_NOT_VISIBLE, 200)

        result, _ = self.agent.score_query(
            query_text="Test?",
            domain="test.com",
            industry="Tech",
            competitors=[],
            search_volume=100,
            difficulty=150,  # out of range
        )
        self.assertLessEqual(result["competitive_difficulty"], 100)


# ── ContentRecommendationAgent tests ─────────────────────────────────────────

class TestContentRecommendationAgent(unittest.TestCase):
    def setUp(self):
        self.agent = ContentRecommendationAgent(api_key="test-key-not-used")

    @patch.object(ContentRecommendationAgent, "_call_llm")
    def test_happy_path(self, mock_llm):
        mock_llm.return_value = (_RECOMMENDATION_PAYLOAD, 1000)

        recs, tokens = self.agent.generate_recommendations(
            name="Surfer SEO",
            domain="surferseo.com",
            industry="SEO Software",
            description="AI content optimisation",
            top_opportunity_queries=[
                {
                    "query_text": "What is the best SEO content optimization tool in 2025?",
                    "estimated_search_volume": 1200,
                    "opportunity_score": 0.81,
                    "competitive_difficulty": 62,
                }
            ],
        )

        self.assertEqual(len(recs), 1)
        self.assertEqual(tokens, 1000)
        self.assertEqual(recs[0]["priority"], "high")
        self.assertEqual(recs[0]["content_type"], "comparison_guide")
        self.assertIn("best seo content tool", recs[0]["target_keywords"])

    @patch.object(ContentRecommendationAgent, "_call_llm")
    def test_empty_queries_short_circuits_without_llm_call(self, mock_llm):
        recs, tokens = self.agent.generate_recommendations(
            name="Test", domain="test.com", industry="Tech",
            description="", top_opportunity_queries=[]
        )
        self.assertEqual(recs, [])
        self.assertEqual(tokens, 0)
        mock_llm.assert_not_called()

    @patch.object(ContentRecommendationAgent, "_call_llm")
    def test_invalid_priority_normalised_to_medium(self, mock_llm):
        payload = json.dumps(
            {
                "recommendations": [
                    {
                        "target_query_text": "some query",
                        "content_type": "blog_post",
                        "title": "A Specific Content Title Here",
                        "rationale": "Detailed reason why this content addresses the gap",
                        "target_keywords": ["kw1", "kw2"],
                        "priority": "URGENT",  # invalid value
                    }
                ]
            }
        )
        mock_llm.return_value = (payload, 500)

        recs, _ = self.agent.generate_recommendations(
            name="Test", domain="test.com", industry="Tech", description="",
            top_opportunity_queries=[{
                "query_text": "some query",
                "estimated_search_volume": 100,
                "opportunity_score": 0.5,
                "competitive_difficulty": 50,
            }],
        )
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["priority"], "medium")

    @patch.object(ContentRecommendationAgent, "_call_llm")
    def test_invalid_content_type_normalised_to_blog_post(self, mock_llm):
        payload = json.dumps(
            {
                "recommendations": [
                    {
                        "target_query_text": "some query",
                        "content_type": "youtube_video",  # not in allowed set
                        "title": "A Valid Title for Content",
                        "rationale": "A solid rationale about the content gap",
                        "target_keywords": ["kw"],
                        "priority": "medium",
                    }
                ]
            }
        )
        mock_llm.return_value = (payload, 400)

        recs, _ = self.agent.generate_recommendations(
            name="Test", domain="test.com", industry="Tech", description="",
            top_opportunity_queries=[{
                "query_text": "some query",
                "estimated_search_volume": 100,
                "opportunity_score": 0.5,
                "competitive_difficulty": 50,
            }],
        )
        self.assertEqual(recs[0]["content_type"], "blog_post")

    @patch.object(ContentRecommendationAgent, "_call_llm")
    def test_recs_missing_title_or_rationale_filtered(self, mock_llm):
        payload = json.dumps(
            {
                "recommendations": [
                    {"target_query_text": "q", "content_type": "blog_post", "title": "", "rationale": "good", "target_keywords": [], "priority": "medium"},
                    {"target_query_text": "q", "content_type": "blog_post", "title": "Good Title", "rationale": "", "target_keywords": [], "priority": "medium"},
                    {"target_query_text": "q", "content_type": "blog_post", "title": "Good Title", "rationale": "Good rationale here", "target_keywords": [], "priority": "medium"},
                ]
            }
        )
        mock_llm.return_value = (payload, 600)

        recs, _ = self.agent.generate_recommendations(
            name="T", domain="t.com", industry="T", description="",
            top_opportunity_queries=[{"query_text": "q", "estimated_search_volume": 100, "opportunity_score": 0.5, "competitive_difficulty": 50}],
        )
        self.assertEqual(len(recs), 1)


# ── Opportunity scoring formula tests ────────────────────────────────────────

class TestOpportunityScoring(unittest.TestCase):

    def test_high_value_query_scores_high(self):
        score, intent = calculate_opportunity_score(
            search_volume=10_000,
            competitive_difficulty=10,
            domain_visible=False,
            query_text="best seo content tool vs clearscope",
        )
        self.assertGreater(score, 0.70)
        self.assertEqual(intent, 1.0)

    def test_low_value_query_scores_low(self):
        score, intent = calculate_opportunity_score(
            search_volume=50,
            competitive_difficulty=95,
            domain_visible=True,
            query_text="what is search engine optimisation",
        )
        self.assertLess(score, 0.40)
        self.assertEqual(intent, 0.2)

    def test_score_always_in_unit_range(self):
        extremes = [
            (0, 0, False, "vs best"),
            (1_000_000, 100, True, "what is"),
            (500, 50, None, "how to use"),
        ]
        for vol, diff, vis, text in extremes:
            score, intent = calculate_opportunity_score(vol, diff, vis, text)
            self.assertGreaterEqual(score, 0.0, f"score < 0 for {(vol, diff, vis, text)}")
            self.assertLessEqual(score, 1.0, f"score > 1 for {(vol, diff, vis, text)}")

    def test_not_visible_scores_higher_than_visible_same_params(self):
        not_vis, _ = calculate_opportunity_score(1000, 40, False, "best tool review")
        visible, _ = calculate_opportunity_score(1000, 40, True, "best tool review")
        self.assertGreater(not_vis, visible)

    def test_zero_volume_does_not_crash(self):
        score, _ = calculate_opportunity_score(0, 50, False, "some query")
        self.assertGreaterEqual(score, 0.0)

    def test_volume_score_is_logarithmic(self):
        low = calculate_volume_score(100)
        mid = calculate_volume_score(1_000)
        high = calculate_volume_score(10_000)
        # Each 10× jump should add roughly the same increment (~0.2)
        self.assertLess(high - mid, 0.30)
        self.assertGreater(high - mid, 0.10)
        self.assertAlmostEqual(mid - low, high - mid, delta=0.05)

    def test_ease_score_inverts_difficulty(self):
        self.assertAlmostEqual(calculate_ease_score(0), 1.0)
        self.assertAlmostEqual(calculate_ease_score(100), 0.0)
        self.assertAlmostEqual(calculate_ease_score(50), 0.5)

    def test_visibility_gap_ordering(self):
        not_vis = calculate_visibility_gap_score(False)
        unknown = calculate_visibility_gap_score(None)
        visible = calculate_visibility_gap_score(True)
        self.assertGreater(not_vis, unknown)
        self.assertGreater(unknown, visible)

    def test_intent_score_ordering(self):
        high = calculate_intent_score("best alternative vs competitor comparison")
        medium = calculate_intent_score("how to use this tool guide")
        low = calculate_intent_score("what is search engine optimisation")
        self.assertGreater(high, medium)
        self.assertGreater(medium, low)


if __name__ == "__main__":
    unittest.main()
