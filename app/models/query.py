import uuid
from datetime import datetime, timezone
from app.extensions import db


class DiscoveredQuery(db.Model):
    __tablename__ = "discovered_queries"

    uuid = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    profile_uuid = db.Column(
        db.String(36), db.ForeignKey("business_profiles.uuid"), nullable=False
    )
    run_uuid = db.Column(db.String(36), db.ForeignKey("pipeline_runs.uuid"), nullable=False)
    query_text = db.Column(db.Text, nullable=False)

    # Populated by Agent 2 (VisibilityScoringAgent)
    estimated_search_volume = db.Column(db.Integer, default=0)
    competitive_difficulty = db.Column(db.Integer, default=50)  # 0-100
    opportunity_score = db.Column(db.Float)  # 0.0-1.0, null until scored
    commercial_intent_score = db.Column(db.Float)  # 0.0-1.0

    # Nullable boolean: None=unknown (pre-scoring), True=visible, False=not visible
    domain_visible = db.Column(db.Boolean, nullable=True, default=None)
    visibility_position = db.Column(db.Integer)  # null if not visible
    visibility_context = db.Column(db.Text)  # excerpt from simulated AI response

    discovered_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_checked_at = db.Column(db.DateTime)

    recommendations = db.relationship(
        "ContentRecommendation",
        backref="discovered_query",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    def _visibility_status(self) -> str:
        if self.domain_visible is None:
            return "unknown"
        return "visible" if self.domain_visible else "not_visible"

    def to_dict(self) -> dict:
        return {
            "query_uuid": self.uuid,
            "profile_uuid": self.profile_uuid,
            "run_uuid": self.run_uuid,
            "query_text": self.query_text,
            "estimated_search_volume": self.estimated_search_volume or 0,
            "competitive_difficulty": self.competitive_difficulty or 50,
            "opportunity_score": round(self.opportunity_score, 3) if self.opportunity_score is not None else None,
            "commercial_intent_score": round(self.commercial_intent_score, 3) if self.commercial_intent_score is not None else None,
            "domain_visible": self.domain_visible,
            "visibility_status": self._visibility_status(),
            "visibility_position": self.visibility_position,
            "visibility_context": self.visibility_context,
            "discovered_at": self.discovered_at.isoformat() + "Z" if self.discovered_at else None,
            "last_checked_at": self.last_checked_at.isoformat() + "Z" if self.last_checked_at else None,
        }
