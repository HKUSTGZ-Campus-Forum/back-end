from __future__ import annotations


CANONICAL_MAJOR_CODES = ["AI", "AMAT", "DSA", "SMMG", "FTEC", "ROAS", "MICS", "SEE"]

MAJOR_METADATA = {
    "AI": {
        "code": "AI",
        "name_zh": "人工智能",
        "name_en": "Artificial Intelligence",
    },
    "AMAT": {
        "code": "AMAT",
        "name_zh": "材料科学与工程",
        "name_en": "Materials Science and Engineering",
    },
    "DSA": {
        "code": "DSA",
        "name_zh": "数据科学与大数据",
        "name_en": "Data Science and Big Data Technology",
    },
    "SMMG": {
        "code": "SMMG",
        "name_zh": "智能制造工程",
        "name_en": "Smart Manufacturing Engineering",
    },
    "FTEC": {
        "code": "FTEC",
        "name_zh": "金融科技",
        "name_en": "Financial Technology",
    },
    "ROAS": {
        "code": "ROAS",
        "name_zh": "机器人工程",
        "name_en": "Robotics",
    },
    "MICS": {
        "code": "MICS",
        "name_zh": "微电子科学与工程",
        "name_en": "Microelectronics Science and Engineering",
    },
    "SEE": {
        "code": "SEE",
        "name_zh": "新能源科学与工程",
        "name_en": "New Energy Science and Engineering",
    },
}

MAJOR_ALIASES = {
    "DSBD": "DSA",
    "SEEN": "SEE",
}


def normalize_major_code(value: str | None) -> str:
    code = str(value or "").strip().upper()
    return MAJOR_ALIASES.get(code, code)


def normalize_target_majors(values: list) -> list[str]:
    normalized = []
    for value in values:
        code = normalize_major_code(value)
        if code in MAJOR_METADATA and code not in normalized:
            normalized.append(code)
        if len(normalized) == 3:
            break
    return normalized
