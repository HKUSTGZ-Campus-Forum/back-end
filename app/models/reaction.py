from datetime import datetime, timezone
from app.extensions import db

class Reaction(db.Model):
    __tablename__ = 'reactions'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('posts.id', ondelete='CASCADE'))
    comment_id = db.Column(db.Integer, db.ForeignKey('comments.id', ondelete='CASCADE'))
    emoji_id = db.Column(db.Integer, db.ForeignKey('reaction_emojis.id', ondelete='RESTRICT'), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        db.UniqueConstraint(
            'user_id', 'post_id', 'comment_id', 'emoji_id',
            name='uq_reactions_user_target_emoji',
            postgresql_where=db.text("post_id IS NOT NULL OR comment_id IS NOT NULL")
        ),
        db.CheckConstraint(
            '(post_id IS NULL AND comment_id IS NOT NULL) OR (post_id IS NOT NULL AND comment_id IS NULL)',
            name='ck_reactions_valid_target'
        ),
        db.Index('idx_reactions_post_id', 'post_id', postgresql_where=db.text("post_id IS NOT NULL")),
        db.Index('idx_reactions_comment_id', 'comment_id', postgresql_where=db.text("comment_id IS NOT NULL")),
    )
    
    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "post_id": self.post_id,
            "comment_id": self.comment_id,
            "emoji_id": self.emoji_id,
            "created_at": self.created_at.isoformat()
        }