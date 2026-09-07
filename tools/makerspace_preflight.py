"""Read-only production account/schema preflight. Run as the DB owner.

Does not import Flask (startup initializers must not run during an audit).
"""
import argparse
import json

from sqlalchemy import create_engine, inspect, text


def inspect_target(connection):
    owners = connection.execute(text("SELECT id, username FROM users WHERE lower(email)=:email AND email_verified=true AND is_deleted=false"), {"email": "fning477@connect.hkust-gz.edu.cn"}).mappings().all()
    names = ["maker_spaces", "maker_deployments", "maker_audit_events", "maker_runtime_sessions", "maker_webhook_deliveries", "maker_workers"]
    existing = set(inspect(connection).get_table_names())
    counts = {name: connection.execute(text(f'SELECT count(*) FROM "{name}"')).scalar_one() if name in existing else None for name in names}
    issues = []
    if len(owners) != 1:
        issues.append("creator must match exactly one active verified account")
    present = set(names) & existing
    if present:
        issues.append("initial installation requires all six MakerSpace tables to be absent; inspect an existing installation separately")
    return {"owner_matches": len(owners), "owner": dict(owners[0]) if len(owners) == 1 else None, "existing_tables": counts, "planned_changes": {"schema_tables": len(names), "new_spaces": 1, "audit_rows": 1, "users_updated": 0, "teamup_runtime_rows_changed": 0}, "issues": issues, "ready": not issues}



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="postgresql+psycopg2:///prod_unikorn", help="local peer-auth DB URL, never put a password in arguments")
    args = parser.parse_args()
    with create_engine(args.database).connect() as connection:
        if connection.dialect.name == "postgresql":
            connection.execute(text("SET TRANSACTION READ ONLY"))
        result = inspect_target(connection)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["ready"]:
            raise SystemExit(1)
