from datetime import datetime, timezone

from app.extensions import db


class HomeCarouselSlide(db.Model):
    __tablename__ = "home_carousel_slides"

    LOCALE_ZH = "zh"
    LOCALE_EN = "en"
    LOCALE_ALL = "all"
    LOCALES = {LOCALE_ZH, LOCALE_EN, LOCALE_ALL}

    VARIANT_IMAGE = "image"
    VARIANT_SCHEDULER = "scheduler"
    VARIANTS = {VARIANT_IMAGE, VARIANT_SCHEDULER}

    id = db.Column(db.Integer, primary_key=True)
    locale = db.Column(db.String(8), nullable=False, default=LOCALE_ALL, index=True)
    image_file_id = db.Column(
        db.Integer,
        db.ForeignKey("files.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    image_path = db.Column(db.String(512), nullable=True)
    alt_text_zh = db.Column(db.String(255), nullable=True)
    alt_text_en = db.Column(db.String(255), nullable=True)
    href = db.Column(db.String(2048), nullable=True)
    presentation_variant = db.Column(
        db.String(40),
        nullable=False,
        default=VARIANT_IMAGE,
    )
    sort_order = db.Column(db.Integer, nullable=False, default=0, index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    is_deleted = db.Column(db.Boolean, nullable=False, default=False, index=True)
    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    deleted_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    deleted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    image_file = db.relationship("File", foreign_keys=[image_file_id])
    creator = db.relationship("User", foreign_keys=[created_by_user_id])
    updater = db.relationship("User", foreign_keys=[updated_by_user_id])
    deleter = db.relationship("User", foreign_keys=[deleted_by_user_id])

    __table_args__ = (
        db.CheckConstraint(
            "locale IN ('zh', 'en', 'all')",
            name="ck_home_carousel_locale",
        ),
        db.CheckConstraint(
            "presentation_variant IN ('image', 'scheduler')",
            name="ck_home_carousel_variant",
        ),
        db.CheckConstraint(
            "NOT (image_file_id IS NOT NULL AND image_path IS NOT NULL)",
            name="ck_home_carousel_image_source",
        ),
        db.Index(
            "idx_home_carousel_public",
            "is_deleted",
            "is_active",
            "locale",
            "sort_order",
        ),
    )

    @property
    def image_url(self):
        if (
            self.image_file_id
            and self.image_file
            and self.image_file.status == "uploaded"
            and not self.image_file.is_deleted
        ):
            return f"/api/files/view/{self.image_file_id}"
        return self.image_path

    def resolve_alt_text(self, locale):
        if locale == self.LOCALE_EN:
            return self.alt_text_en or self.alt_text_zh or ""
        return self.alt_text_zh or self.alt_text_en or ""

    def to_public_dict(self, locale):
        return {
            "id": self.id,
            "locale": self.locale,
            "image_url": self.image_url,
            "alt_text": self.resolve_alt_text(locale),
            "href": self.href,
            "presentation_variant": self.presentation_variant,
            "sort_order": self.sort_order,
        }

    def to_admin_dict(self):
        return {
            "id": self.id,
            "locale": self.locale,
            "image_file_id": self.image_file_id,
            "image_path": self.image_path,
            "image_url": self.image_url,
            "alt_text_zh": self.alt_text_zh,
            "alt_text_en": self.alt_text_en,
            "href": self.href,
            "presentation_variant": self.presentation_variant,
            "sort_order": self.sort_order,
            "is_active": self.is_active,
            "is_deleted": self.is_deleted,
            "created_by_user_id": self.created_by_user_id,
            "updated_by_user_id": self.updated_by_user_id,
            "deleted_by_user_id": self.deleted_by_user_id,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
