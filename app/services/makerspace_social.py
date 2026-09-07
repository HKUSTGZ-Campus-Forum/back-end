"""Public space reactions and owner-controlled display covers, separate from runtime settings."""
from sqlalchemy import func
from app.extensions import db
from app.models.file import File
from app.models.makerspace import MakerLike, MakerFavorite
from app.services.makerspace_service import MakerError, audit


def decorate(values, user=None):
    ids = [value["id"] for value in values]
    if not ids:
        return values
    active = bool(user and not user.is_deleted and user.email_verified)
    for model, count_key, state_key in ((MakerLike, "likes_count", "is_liked"), (MakerFavorite, "favorites_count", "is_favorited")):
        counts = dict(db.session.query(model.space_id, func.count()).filter(model.space_id.in_(ids)).group_by(model.space_id).all())
        selected = {row[0] for row in db.session.query(model.space_id).filter(model.space_id.in_(ids), model.user_id == user.id).all()} if active else set()
        for value in values:
            value[count_key] = counts.get(value["id"], 0)
            value[state_key] = value["id"] in selected
    return values


def set_cover(space, user, file_id):
    if file_id is not None:
        if type(file_id) is not int:
            raise MakerError("invalid_cover")
        record = File.query.filter_by(id=file_id, user_id=user.id, file_type=File.MAKER_COVER, entity_type="makerspace", entity_id=None, status="uploaded", is_deleted=False).with_for_update().first()
        if not record or record.mime_type not in File.MAKER_COVER_MIMES or not record.file_size or record.file_size > File.MAX_MAKER_COVER_BYTES:
            raise MakerError("invalid_cover")
    if space.cover_file_id == file_id:
        return
    space.cover_file_id = file_id
    audit(space, user, "cover_updated", file_id=file_id)
