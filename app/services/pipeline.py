"""
Pipeline Orchestrator
=====================

Coordinates the three-agent AI visibility pipeline in sequence:

  Agent 1 (QueryDiscoveryAgent)
      ↓  10-20 discovered queries
  Agent 2 (VisibilityScoringAgent)    ← per-query, continues on partial failure
      ↓  scored + visibility data
  Agent 3 (ContentRecommendationAgent)
      ↓  content recommendations for top-N not-visible queries

Partial failures are isolated at the query level (Agent 2) so one bad LLM
response doesn't abort the whole run.
"""

import logging
from datetime import datetime, timezone

from flask import current_app

from app.agents.discovery import QueryDiscoveryAgent
from app.agents.recommendation import ContentRecommendationAgent
from app.agents.scoring import VisibilityScoringAgent
from app.extensions import db
from app.models.pipeline import PipelineRun
from app.models.query import DiscoveredQuery
from app.models.recommendation import ContentRecommendation
from app.services.dataforseo import DataForSEOClient
from app.utils.scoring import calculate_opportunity_score

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """
    Instantiate per-request — holds the three agent instances and the optional
    DataForSEO client so they can be tested independently.
    """

    def __init__(self, app_config: dict):
        api_key = app_config.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required to run the pipeline")

        dfs_login = app_config.get("DATAFORSEO_LOGIN")
        dfs_password = app_config.get("DATAFORSEO_PASSWORD")
        dataforseo_client = None
        if dfs_login and dfs_password:
            dataforseo_client = DataForSEOClient(dfs_login, dfs_password)
            logger.info("DataForSEO client initialised with real credentials")
        else:
            logger.warning("DataForSEO credentials not configured — using heuristic fallback")

        self.discovery_agent = QueryDiscoveryAgent(api_key)
        self.scoring_agent = VisibilityScoringAgent(api_key, dataforseo_client)
        self.recommendation_agent = ContentRecommendationAgent(api_key)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, profile) -> PipelineRun:
        """
        Execute the full pipeline synchronously.
        Always returns a PipelineRun (status=completed or failed).
        """
        run = PipelineRun(profile_uuid=profile.uuid, status="running")
        db.session.add(run)
        db.session.commit()

        total_tokens = 0

        try:
            total_tokens = self._run_pipeline(profile, run, total_tokens)
        except Exception as exc:
            logger.error("Pipeline %s: fatal error — %s", run.uuid, exc, exc_info=True)
            run.status = "failed"
            run.error_message = str(exc)
            run.tokens_used = total_tokens
            run.completed_at = datetime.now(timezone.utc)
            db.session.commit()

        return run

    # ------------------------------------------------------------------
    # Internal steps
    # ------------------------------------------------------------------

    def _run_pipeline(self, profile, run: PipelineRun, total_tokens: int) -> int:
        """Runs all three agents; raises on unrecoverable errors."""

        # ── Agent 1: Query Discovery ──────────────────────────────────────────
        logger.info("Pipeline %s: Agent 1 — Query Discovery", run.uuid)
        max_queries = current_app.config.get("PIPELINE_MAX_QUERIES", 15)
        queries_data, tokens = self.discovery_agent.discover_queries(
            name=profile.name,
            domain=profile.domain,
            industry=profile.industry,
            description=profile.description or "",
            competitors=profile.competitors or [],
            num_queries=max_queries,
        )
        total_tokens += tokens

        if not queries_data:
            raise RuntimeError("Agent 1 returned no queries — check your ANTHROPIC_API_KEY and prompt")

        # Persist bare query records (unscored)
        db_query_pairs: list[tuple[DiscoveredQuery, dict]] = []
        for q_data in queries_data:
            query = DiscoveredQuery(
                profile_uuid=profile.uuid,
                run_uuid=run.uuid,
                query_text=q_data["query_text"],
            )
            db.session.add(query)
            db_query_pairs.append((query, q_data))

        db.session.flush()
        run.queries_discovered = len(db_query_pairs)
        db.session.commit()
        logger.info("Pipeline %s: Agent 1 complete — %d queries", run.uuid, len(db_query_pairs))

        # ── Agent 2: Visibility Scoring ───────────────────────────────────────
        logger.info("Pipeline %s: Agent 2 — Visibility Scoring (%d queries)", run.uuid, len(db_query_pairs))
        scored_count = 0

        for query, _ in db_query_pairs:
            try:
                score_result, tokens = self.scoring_agent.score_query(
                    query_text=query.query_text,
                    domain=profile.domain,
                    industry=profile.industry,
                    competitors=profile.competitors or [],
                )
                total_tokens += tokens

                opp_score, intent_score = calculate_opportunity_score(
                    search_volume=score_result["search_volume"],
                    competitive_difficulty=score_result["competitive_difficulty"],
                    domain_visible=score_result["domain_visible"],
                    query_text=query.query_text,
                )

                query.estimated_search_volume = score_result["search_volume"]
                query.competitive_difficulty = score_result["competitive_difficulty"]
                query.domain_visible = score_result["domain_visible"]
                query.visibility_position = score_result["visibility_position"]
                query.visibility_context = score_result.get("visibility_context")
                query.opportunity_score = opp_score
                query.commercial_intent_score = intent_score
                query.last_checked_at = datetime.now(timezone.utc)
                scored_count += 1

            except Exception as exc:
                # Partial failure isolation — log and continue
                logger.warning(
                    "Pipeline %s: Agent 2 failed for query '%.50s': %s",
                    run.uuid,
                    query.query_text,
                    exc,
                )
                # Compute a baseline score using only query text signals
                opp_score, intent_score = calculate_opportunity_score(
                    search_volume=0,
                    competitive_difficulty=50,
                    domain_visible=None,
                    query_text=query.query_text,
                )
                query.opportunity_score = opp_score
                query.commercial_intent_score = intent_score

        db.session.commit()
        run.queries_scored = scored_count
        logger.info(
            "Pipeline %s: Agent 2 complete — %d/%d scored",
            run.uuid, scored_count, len(db_query_pairs),
        )

        # ── Agent 3: Content Recommendations ─────────────────────────────────
        logger.info("Pipeline %s: Agent 3 — Content Recommendations", run.uuid)
        top_n = current_app.config.get("PIPELINE_TOP_QUERIES_FOR_RECOMMENDATIONS", 5)

        opportunity_queries = (
            DiscoveredQuery.query.filter_by(
                profile_uuid=profile.uuid,
                run_uuid=run.uuid,
                domain_visible=False,
            )
            .order_by(DiscoveredQuery.opportunity_score.desc())
            .limit(top_n)
            .all()
        )

        recs_data, tokens = self.recommendation_agent.generate_recommendations(
            name=profile.name,
            domain=profile.domain,
            industry=profile.industry,
            description=profile.description or "",
            top_opportunity_queries=[q.to_dict() for q in opportunity_queries],
        )
        total_tokens += tokens

        # Map each recommendation back to its source query
        text_to_uuid = {q.query_text: q.uuid for q in opportunity_queries}

        for rec_data in recs_data:
            target_text = rec_data.get("target_query_text", "")
            query_uuid = text_to_uuid.get(target_text)

            # Fuzzy fallback: substring match
            if not query_uuid:
                for q in opportunity_queries:
                    if target_text.lower() in q.query_text.lower() or q.query_text.lower() in target_text.lower():
                        query_uuid = q.uuid
                        break

            # Last resort: assign to highest-opportunity query
            if not query_uuid and opportunity_queries:
                query_uuid = opportunity_queries[0].uuid

            if not query_uuid:
                continue

            rec = ContentRecommendation(
                profile_uuid=profile.uuid,
                query_uuid=query_uuid,
                content_type=rec_data.get("content_type", "blog_post"),
                title=rec_data["title"],
                rationale=rec_data["rationale"],
                target_keywords=rec_data.get("target_keywords", []),
                priority=rec_data.get("priority", "medium"),
            )
            db.session.add(rec)

        db.session.commit()
        logger.info(
            "Pipeline %s: Agent 3 complete — %d recommendations", run.uuid, len(recs_data)
        )

        # ── Finalise run ──────────────────────────────────────────────────────
        run.status = "completed"
        run.tokens_used = total_tokens
        run.completed_at = datetime.now(timezone.utc)
        db.session.commit()
        logger.info("Pipeline %s: DONE — total tokens %d", run.uuid, total_tokens)

        return total_tokens
