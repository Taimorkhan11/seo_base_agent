from flask import Blueprint, jsonify, request, current_app

from app.extensions import db
from app.models.profile import BusinessProfile
from app.models.query import DiscoveredQuery
from app.models.recommendation import ContentRecommendation
from app.services.pipeline import PipelineOrchestrator

profiles_bp = Blueprint("profiles", __name__)

_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _err(message: str, code: int):
    return jsonify({"error": message, "status": code}), code


# ── POST /api/v1/profiles ─────────────────────────────────────────────────────

@profiles_bp.route("/profiles", methods=["POST"])
def create_profile():
    data = request.get_json(silent=True)
    if not data:
        return _err("Request body must be valid JSON", 400)

    missing = [f for f in ("name", "domain", "industry") if not data.get(f)]
    if missing:
        return _err(f"Missing required fields: {', '.join(missing)}", 400)

    competitors = data.get("competitors", [])
    if not isinstance(competitors, list):
        return _err("'competitors' must be a JSON array of domain strings", 400)

    profile = BusinessProfile(
        name=data["name"].strip(),
        domain=data["domain"].strip().lower().rstrip("/"),
        industry=data["industry"].strip(),
        description=(data.get("description") or "").strip(),
        competitors=[str(c).strip().lower() for c in competitors],
    )
    db.session.add(profile)
    db.session.commit()

    return (
        jsonify(
            {
                "profile_uuid": profile.uuid,
                "name": profile.name,
                "domain": profile.domain,
                "status": profile.status,
                "created_at": profile.created_at.isoformat() + "Z",
            }
        ),
        201,
    )


# ── GET /api/v1/profiles/<uuid> ───────────────────────────────────────────────

@profiles_bp.route("/profiles/<profile_uuid>", methods=["GET"])
def get_profile(profile_uuid):
    profile = BusinessProfile.query.get(profile_uuid)
    if not profile:
        return _err(f"Profile '{profile_uuid}' not found", 404)
    return jsonify(profile.to_dict()), 200


# ── POST /api/v1/profiles/<uuid>/run ─────────────────────────────────────────

@profiles_bp.route("/profiles/<profile_uuid>/run", methods=["POST"])
def run_pipeline(profile_uuid):
    profile = BusinessProfile.query.get(profile_uuid)
    if not profile:
        return _err(f"Profile '{profile_uuid}' not found", 404)

    try:
        orchestrator = PipelineOrchestrator(current_app.config)
    except ValueError as exc:
        return _err(str(exc), 503)

    run = orchestrator.run(profile)

    top_queries = (
        DiscoveredQuery.query.filter_by(profile_uuid=profile_uuid, run_uuid=run.uuid)
        .order_by(DiscoveredQuery.opportunity_score.desc())
        .limit(3)
        .all()
    )

    recommendations = (
        ContentRecommendation.query.filter_by(profile_uuid=profile_uuid)
        .order_by(ContentRecommendation.created_at.desc())
        .limit(20)
        .all()
    )
    recommendations.sort(
        key=lambda r: (_PRIORITY_ORDER.get(r.priority, 1), -(r.created_at.timestamp() if r.created_at else 0))
    )

    return (
        jsonify(
            {
                "run_uuid": run.uuid,
                "profile_uuid": profile_uuid,
                "status": run.status,
                "error_message": run.error_message,
                "queries_discovered": run.queries_discovered,
                "queries_scored": run.queries_scored,
                "tokens_used": run.tokens_used,
                "top_3_opportunity_queries": [q.to_dict() for q in top_queries],
                "content_recommendations": [r.to_dict() for r in recommendations],
                "started_at": run.started_at.isoformat() + "Z" if run.started_at else None,
                "completed_at": run.completed_at.isoformat() + "Z" if run.completed_at else None,
            }
        ),
        200,
    )


# ── GET /api/v1/profiles/<uuid>/queries ───────────────────────────────────────

@profiles_bp.route("/profiles/<profile_uuid>/queries", methods=["GET"])
def get_queries(profile_uuid):
    if not BusinessProfile.query.get(profile_uuid):
        return _err(f"Profile '{profile_uuid}' not found", 404)

    q = DiscoveredQuery.query.filter_by(profile_uuid=profile_uuid)

    min_score = request.args.get("min_score", type=float)
    if min_score is not None:
        q = q.filter(DiscoveredQuery.opportunity_score >= min_score)

    status_filter = request.args.get("status")
    if status_filter == "visible":
        q = q.filter(DiscoveredQuery.domain_visible.is_(True))
    elif status_filter == "not_visible":
        q = q.filter(DiscoveredQuery.domain_visible.is_(False))
    elif status_filter == "unknown":
        q = q.filter(DiscoveredQuery.domain_visible.is_(None))

    q = q.order_by(DiscoveredQuery.opportunity_score.desc())

    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 20, type=int), 100)
    paginated = q.paginate(page=page, per_page=per_page, error_out=False)

    return (
        jsonify(
            {
                "profile_uuid": profile_uuid,
                "queries": [item.to_dict() for item in paginated.items],
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": paginated.total,
                    "pages": paginated.pages,
                    "has_next": paginated.has_next,
                    "has_prev": paginated.has_prev,
                },
            }
        ),
        200,
    )


# ── GET /api/v1/profiles/<uuid>/recommendations ───────────────────────────────

@profiles_bp.route("/profiles/<profile_uuid>/recommendations", methods=["GET"])
def get_recommendations(profile_uuid):
    if not BusinessProfile.query.get(profile_uuid):
        return _err(f"Profile '{profile_uuid}' not found", 404)

    recs = ContentRecommendation.query.filter_by(profile_uuid=profile_uuid).all()
    recs.sort(
        key=lambda r: (_PRIORITY_ORDER.get(r.priority, 1), -(r.created_at.timestamp() if r.created_at else 0))
    )

    return (
        jsonify(
            {
                "profile_uuid": profile_uuid,
                "recommendations": [r.to_dict() for r in recs],
                "total": len(recs),
            }
        ),
        200,
    )
