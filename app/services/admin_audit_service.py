from app.extensions import db
from app.models.admin_audit_log import AdminAuditLog


def log_admin_action(
    actor,
    action,
    target_type,
    target_id=None,
    target_label=None,
    note=None,
    metadata=None,
):
    log = AdminAuditLog(
        actor_user_id=actor.id if actor else None,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_label=(target_label or None),
        note=note,
        metadata_json=metadata or {},
    )
    db.session.add(log)
    return log
