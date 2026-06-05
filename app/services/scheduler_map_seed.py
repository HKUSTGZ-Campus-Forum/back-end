import hashlib
import json
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.scheduler_map import SchedulerMapComponent, SchedulerMapLine


BUNDLED_SCHEDULER_MAP_SEED_FILE = Path(__file__).resolve().parents[1] / "data" / "scheduler_map_seed.json"
BUNDLED_SCHEDULER_MAP_SEED_SHA256 = "6337f71e38bab1154ee2d808f90a40d0337efe6fccd14d6d2244ec0d7d2dde7a"


def file_sha256(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_scheduler_map_seed(file_path: Path = BUNDLED_SCHEDULER_MAP_SEED_FILE) -> dict:
    with file_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("scheduler map seed must be a JSON object")
    components = data.get("components")
    lines = data.get("lines")
    if not isinstance(components, list) or not isinstance(lines, list):
        raise ValueError("scheduler map seed requires components and lines lists")

    component_ids = set()
    for component in components:
        component_id = component.get("id")
        if not component_id:
            raise ValueError("scheduler map component is missing id")
        if component_id in component_ids:
            raise ValueError(f"duplicate scheduler map component id: {component_id}")
        component_ids.add(component_id)

    for line in lines:
        start_id = line.get("start_id")
        end_id = line.get("end_id")
        if start_id not in component_ids or end_id not in component_ids:
            raise ValueError(f"scheduler map line references missing component: {start_id}->{end_id}")

    return data


def seed_scheduler_map_if_empty(seed: dict) -> dict:
    existing_components = SchedulerMapComponent.query.count()
    existing_lines = SchedulerMapLine.query.count()
    if existing_components or existing_lines:
        return {
            "status": "skipped",
            "components": existing_components,
            "lines": existing_lines,
        }

    try:
        for component in seed["components"]:
            db.session.add(SchedulerMapComponent(
                id=component["id"],
                node_type=component.get("node_type"),
                x_coordinate=int(component["x_coordinate"]),
                y_coordinate=int(component["y_coordinate"]),
                category=int(component["category"]),
            ))
        db.session.flush()

        for line in seed["lines"]:
            db.session.add(SchedulerMapLine(
                start_id=line["start_id"],
                end_id=line["end_id"],
                line_type=line.get("line_type"),
                x_coordinate=int(line["x_coordinate"]),
                category=int(line["category"]),
            ))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return {
            "status": "skipped",
            "components": SchedulerMapComponent.query.count(),
            "lines": SchedulerMapLine.query.count(),
        }
    except Exception:
        db.session.rollback()
        raise

    return {
        "status": "seeded",
        "components": len(seed["components"]),
        "lines": len(seed["lines"]),
    }


def seed_bundled_scheduler_map_if_empty() -> dict:
    actual_hash = file_sha256(BUNDLED_SCHEDULER_MAP_SEED_FILE)
    if actual_hash != BUNDLED_SCHEDULER_MAP_SEED_SHA256:
        raise ValueError(
            "Bundled scheduler map seed hash mismatch: "
            f"{actual_hash} != {BUNDLED_SCHEDULER_MAP_SEED_SHA256}"
        )
    return seed_scheduler_map_if_empty(load_scheduler_map_seed())
