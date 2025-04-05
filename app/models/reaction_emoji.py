from datetime import datetime, timezone
from app.extensions import db

class ReactionEmoji(db.Model):
    __tablename__ = 'reaction_emojis'
    
    id = db.Column(db.Integer, primary_key=True)
    emoji_code = db.Column(db.String(50), nullable=False, unique=True)
    description = db.Column(db.Text)
    image_url = db.Column(db.Text)
    display_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    
    # Relationships
    reactions = db.relationship('Reaction', backref='emoji', lazy='dynamic')
    
    def to_dict(self):
        return {
            "id": self.id,
            "emoji_code": self.emoji_code,
            "description": self.description,
            "image_url": self.image_url,
            "display_order": self.display_order,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat()
        }
    
    # Add these constraints to the model
    __table_args__ = (
        db.Index(
            'idx_reaction_emojis_active',
            'display_order',
            postgresql_where=db.text("is_active IS TRUE")
        ),
    )