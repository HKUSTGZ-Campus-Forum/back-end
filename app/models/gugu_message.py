from datetime import datetime, timezone
from app.extensions import db
from sqlalchemy import or_, and_

class GuguMessage(db.Model):
    """咕咕聊天室消息模型"""
    __tablename__ = 'gugu_messages'
    
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    display_identity_id = db.Column(db.Integer, db.ForeignKey('user_identities.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    # 关系
    author = db.relationship('User', backref=db.backref('gugu_messages', lazy=True))
    display_identity = db.relationship('UserIdentity', foreign_keys=[display_identity_id])
    
    def __repr__(self):
        return f'<GuguMessage {self.id}>'
    
    def to_dict(self):
        """转换为字典格式"""
        data = {
            'id': self.id,
            'content': self.content,
            'author_id': self.author_id,
            'author': self.author.username if self.author else None,
            'author_avatar': self.author.avatar_url if self.author else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
        
        # Include display identity if present
        if self.display_identity and self.display_identity.is_active():
            data["display_identity"] = {
                "id": self.display_identity.id,
                "type": self.display_identity.identity_type.to_dict() if self.display_identity.identity_type else None
            }
        
        return data
    
    @classmethod
    def get_recent_messages(cls, limit=50):
        """获取最近的消息（按时间降序，最新在前）"""
        return cls.query.order_by(cls.created_at.desc()).limit(limit).all()

    @classmethod
    def get_messages_desc_paginated(cls, limit=50, before_id=None):
        """
        主页时间线：按 created_at、id 降序；before_id 为当前已展示的最老一条的 id，
        返回严格比其更老的消息。多取 1 条用于判断 has_more。
        """
        safe_limit = max(1, min(int(limit), 100))
        fetch_n = safe_limit + 1
        q = cls.query
        if before_id is not None:
            anchor = cls.query.get(before_id)
            if anchor is None:
                return [], False
            q = q.filter(
                or_(
                    cls.created_at < anchor.created_at,
                    and_(cls.created_at == anchor.created_at, cls.id < anchor.id),
                )
            )
        rows = (
            q.order_by(cls.created_at.desc(), cls.id.desc()).limit(fetch_n).all()
        )
        has_more = len(rows) > safe_limit
        return rows[:safe_limit], has_more
    
    @classmethod
    def create_message(cls, content, author_id):
        """创建新消息"""
        message = cls(
            content=content,
            author_id=author_id
        )
        db.session.add(message)
        db.session.commit()
        return message