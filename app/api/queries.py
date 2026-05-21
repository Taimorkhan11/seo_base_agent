from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify

from app.agents.scoring import VisibilityScoringAgent
from app.extensions import db
from app.models.profile import BusinessProfile
from app.models.query import DiscoveredQuery
from app.services.dataforseo import DataForSEOClient
from app.utils.scoring import calculate_opportunity_score

queries_bp = Blueprint("queries", __name__)


def _err(message: str, code: int):
    return jsonify({"error": message, "status": code}), code


# ── POST /api/v1/queries/<uuid>/recheck ──────────────────────────────────────

@queries_bp.route("/queries/<query_uuid>/recheck", methods=["POST"])
def recheck_query(query_uuid):
    """
    Re-run Agent 2 (VisibilityScoringAgent) on a single query.
    Useful after publishing content to see if visibility has improved.
    """
    query = DiscoveredQuery.query.get(query_uuid)
    if not query:
        return _err(f"Query '{query_uuid}' not found", 404)

    profile = BusinessProfile.query.get(query.profile_uuid)
    if not profile:
        return _err("Associated profile not found", 404)

    api_key = current_app.config.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _err("ANTHROPIC_API_KEY not configured", 503)

    dfs_login = current_app.config.get("DATAFORSEO_LOGIN")
    dfs_password = current_app.config.get("DATAFORSEO_PASSWORD")
    dataforseo_client = DataForSEOClient(dfs_login, dfs_password) if (dfs_login and dfs_password) else None

    agent = VisibilityScoringAgent(api_key, dataforseo_client)

    try:
        score_result, tokens = agent.score_query(
            query_text=query.query_text,
            domain=profile.domain,
            industry=profile.industry,
            competitors=profile.competitors or [],
        )
    except Exception as exc:
        return _err(f"Scoring failed: {exc}", 500)

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
    db.session.commit()

    return (
        jsonify(
            {
                "query_uuid": query_uuid,
                "updated": True,
                "tokens_used": tokens,
                "query": query.to_dict(),
            }
        ),
        200,
    )
