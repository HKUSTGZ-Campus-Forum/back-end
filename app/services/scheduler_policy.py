from __future__ import annotations

import re
from typing import Any, Iterable

from app.services.course_domain import normalize_course_code


MODULE_RE = re.compile(r"^M\d{2}$", re.IGNORECASE)
MODULE_CREDIT_RE = re.compile(r"\s*·\s*KLMS module credit:\s*(\d+)\s*$", re.IGNORECASE)

# These are academic selection rules, not teaching-period groupings. A module's
# layer may describe when it is taught, but never which other module it must be
# paired with.
MODULAR_COURSE_RULES: dict[str, tuple[dict[str, Any], ...]] = {
    "UCUG1000": (
        {
            "id": "required",
            "role": "required",
            "min_select": 1,
            "max_select": 1,
            "module_codes": ("M01",),
        },
        {
            "id": "electives",
            "role": "elective",
            "min_select": 2,
            "max_select": 2,
            "module_codes": ("M02", "M03", "M04", "M05", "M06", "M07"),
        },
    ),
    "UCUG1002": (
        {
            "id": "required",
            "role": "required",
            "min_select": 1,
            "max_select": 1,
            "module_codes": ("M01",),
        },
        {
            "id": "electives",
            "role": "elective",
            "min_select": 1,
            "max_select": 1,
            "module_codes": ("M02", "M03", "M04", "M05", "M06"),
        },
    ),
}


def _value(section: Any, name: str, default: Any = None) -> Any:
    if isinstance(section, dict):
        return section.get(name, default)
    return getattr(section, name, default)


def course_credit_policy(subject: str | None, credit: int | float | None) -> dict[str, Any]:
    academic_credit = credit or 0
    counts_toward_term_load = str(subject or "").strip().upper() != "MOES"
    return {
        "credit": academic_credit,
        "counts_toward_term_load": counts_toward_term_load,
        "term_load_credit": academic_credit if counts_toward_term_load else 0,
    }


def _module_metadata(module_code: str, sections: Iterable[Any]) -> dict[str, Any]:
    matching = [
        section
        for section in sections
        if str(_value(section, "section_type", "")).strip().upper() == module_code
    ]
    remarks = str(_value(matching[0], "remarks", "") or "").strip() if matching else ""
    credit_match = MODULE_CREDIT_RE.search(remarks)
    title = MODULE_CREDIT_RE.sub("", remarks).strip() or module_code
    return {
        "code": module_code,
        "title": title,
        "credit": int(credit_match.group(1)) if credit_match else None,
        "available": bool(matching),
    }


def course_selection_policy(course_code: str, sections: Iterable[Any]) -> dict[str, Any]:
    normalized_code = normalize_course_code(course_code)
    section_list = list(sections)
    configured_groups = MODULAR_COURSE_RULES.get(normalized_code)
    if configured_groups is not None:
        module_codes = tuple(
            dict.fromkeys(
                module_code
                for group in configured_groups
                for module_code in group["module_codes"]
            )
        )
        return {
            "kind": "module",
            "groups": [
                {
                    **{key: value for key, value in group.items() if key != "module_codes"},
                    "module_codes": list(group["module_codes"]),
                }
                for group in configured_groups
            ],
            "modules": [_module_metadata(code, section_list) for code in module_codes],
        }

    layers = sorted({int(_value(section, "layer", 0)) for section in section_list})
    return {
        "kind": "layer",
        "groups": [
            {
                "id": f"layer-{layer}",
                "role": "section",
                "min_select": 1,
                "max_select": 1,
                "layers": [layer],
            }
            for layer in layers
        ],
        "modules": [],
    }


def module_code_for_sections(sections: Iterable[Any]) -> str | None:
    module_codes = {
        str(_value(section, "section_type", "")).strip().upper()
        for section in sections
    }
    if len(module_codes) != 1:
        return None
    module_code = next(iter(module_codes))
    return module_code if MODULE_RE.fullmatch(module_code) else None
