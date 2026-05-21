import uuid
from datetime import datetime, timezone
from app.extensions import db


class ContentRecommendation(db.Model):
    __tablename__ = "content_recommendations"

    uuid = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    profile_uuid = db.Column(
        db.String(36), db.ForeignKey("business_profiles.uuid"), nullable=False
    )
    query_uuid = db.Column(
        db.String(36), db.ForeignKey("discovered_queries.uuid"), nullable=False
    )
    # blog_post | landing_page | faq | comparison_guide | case_study | resource_hub
    content_type = db.Column(db.String(100), default="blog_post")
    title = db.Column(db.String(500), nullable=False)
    rationale = db.Column(db.Text, nullable=False)
    target_keywords = db.Column(db.JSON, default=list)
    priority = db.Column(db.String(20), default="medium")  # high | medium | low
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "recommendation_uuid": self.uuid,
            "profile_uuid": self.profile_uuid,
            "target_query_uuid": self.query_uuid,
            "content_type": self.content_type,
            "title": self.title,
            "rationale": self.rationale,
            "target_keywords": self.target_keywords or [],
            "priority": self.priority,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
        }
