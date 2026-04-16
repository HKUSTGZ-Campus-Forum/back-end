"""
Semester utilities for handling semester codes and conversions.
Supports internationalization by separating codes from display names.
"""

from datetime import datetime
from typing import Dict, List, Optional, Tuple
import re

class SemesterCode:
    """Standard semester codes for consistent data handling"""
    SPRING = "spring"
    SUMMER = "summer" 
    FALL = "fall"
    WINTER = "winter"

# Mapping from legacy Chinese characters to standard codes
CHINESE_TO_CODE = {
    '春': SemesterCode.SPRING,
    '夏': SemesterCode.SUMMER, 
    '秋': SemesterCode.FALL,
    '冬': SemesterCode.WINTER
}

# Mapping from standard codes to display names (can be extended for i18n)
CODE_TO_DISPLAY = {
    'en': {
        SemesterCode.SPRING: 'Spring',
        SemesterCode.SUMMER: 'Summer',
        SemesterCode.FALL: 'Fall',
        SemesterCode.WINTER: 'Winter'
    },
    'zh': {
        SemesterCode.SPRING: '春',
        SemesterCode.SUMMER: '夏',
        SemesterCode.FALL: '秋',
        SemesterCode.WINTER: '冬'
    }
}

# Semester ordering within one academic year.
# Academic year runs chronologically as: fall -> winter -> spring -> summer.
# ``sort_semesters`` returns newest first, so summer should appear before spring
# and fall when the anchor year is the same.
SEMESTER_ORDER = {
    SemesterCode.FALL: 1,
    SemesterCode.WINTER: 2,
    SemesterCode.SPRING: 3,
    SemesterCode.SUMMER: 4,
}

def parse_semester_tag(tag_name: str) -> Optional[Tuple[str, str, str]]:
    """
    Parse a semester tag and return (course_code, year, semester_code).
    
    Examples:
        "AIAA 1010-2024fall" -> ("AIAA 1010", "2024", "fall")
        "AIAA 1010-24Fall" -> ("AIAA 1010", "24", "fall") 
        "AIAA 1010-2024春" -> ("AIAA 1010", "2024", "spring")
    
    Returns None if tag doesn't match semester pattern.
    """
    if '-' not in tag_name:
        return None
    
    try:
        course_code, semester_part = tag_name.split('-', 1)
        
        # Extract year and season from semester part
        # Support patterns like: 2024fall, 24Fall, 2024春, etc.
        match = re.match(r'^(\d{2,4})(.+)$', semester_part)
        if not match:
            return None
            
        year_str, season_str = match.groups()
        
        # Normalize year (convert 2-digit to 4-digit)
        if len(year_str) == 2:
            year_prefix = "20" if int(year_str) < 50 else "19"
            year = year_prefix + year_str
        else:
            year = year_str
            
        # Normalize season to standard code
        season_lower = season_str.lower()
        
        # Check if it's already a standard code
        if season_lower in [SemesterCode.SPRING, SemesterCode.SUMMER, SemesterCode.FALL, SemesterCode.WINTER]:
            semester_code = season_lower
        # Check if it's a Chinese character
        elif season_str in CHINESE_TO_CODE:
            semester_code = CHINESE_TO_CODE[season_str]
        # Check common English abbreviations
        elif season_lower in ['spr', 'spring']:
            semester_code = SemesterCode.SPRING
        elif season_lower in ['sum', 'summer']:
            semester_code = SemesterCode.SUMMER
        elif season_lower in ['fal', 'fall', 'autumn']:
            semester_code = SemesterCode.FALL
        elif season_lower in ['win', 'winter']:
            semester_code = SemesterCode.WINTER
        else:
            return None
            
        return course_code.strip(), year, semester_code
        
    except (ValueError, AttributeError):
        return None

def format_semester_tag(course_code: str, year: str, semester_code: str, use_legacy_format: bool = True) -> str:
    """
    Format a semester tag, optionally using legacy format.
    
    Args:
        course_code: Course code (e.g., "AIAA 1010")
        year: Year as string (e.g., "2024")
        semester_code: Standard semester code (e.g., "fall")
        use_legacy_format: If True, use format like "AIAA 1010-24Fall"
    
    Returns:
        Formatted tag (e.g., "AIAA 1010-24Fall" or "AIAA 1010-2024fall")
    """
    if use_legacy_format:
        # Convert to legacy format: 2-digit year + capitalized season
        year_2digit = year[-2:] if len(year) == 4 else year
        season_capitalized = semester_code.capitalize()
        return f"{course_code}-{year_2digit}{season_capitalized}"
    else:
        return f"{course_code}-{year}{semester_code}"

def get_semester_display_name(semester_code: str, language: str = 'zh') -> str:
    """
    Get display name for a semester code.
    
    Args:
        semester_code: Standard semester code
        language: Language code ('en' or 'zh')
    
    Returns:
        Localized display name
    """
    return CODE_TO_DISPLAY.get(language, {}).get(semester_code, semester_code)


def format_academic_year_semester_display(year: str, semester_code: str, language: str = 'zh') -> str:
    """
    Academic-year style label (less ambiguous than '2025秋').

    ``year`` is the anchor from tags (4-digit string), ``semester_code`` is a standard
    code (spring/fall/...).

    与秋、夏一致：用标签中的年份 Y 作为学年起始年，展示为 ``YY-(Y+1)末两位`` + 季节。
    例如 ``2024spring`` → ``24-25春``，``2025fall`` → ``25-26秋``；与同一学年下的
    ``2025spring``（25-26 春）共用同一套起算方式。
    """
    y = int(year)
    season_label = get_semester_display_name(semester_code, language)

    y0, y1 = y, y + 1

    yy0 = y0 % 100
    yy1 = y1 % 100

    if language == 'zh':
        return f"{yy0:02d}-{yy1:02d}{season_label}"
    return f"{yy0:02d}-{yy1:02d} {season_label}"


def format_offering_display_tag(year: str, semester_code: str) -> str:
    """
    Format an offering tag used in routes and standalone semester post tags.

    Examples:
        "2025", "fall" -> "25-26Fall"
        "2024", "spring" -> "24-25Spring"
    """
    y = int(year)
    yy0 = y % 100
    yy1 = (y + 1) % 100
    season_label = get_semester_display_name(semester_code, 'en')
    return f"{yy0:02d}-{yy1:02d}{season_label}"


def parse_offering_display_tag(tag_name: str) -> Optional[Tuple[str, str]]:
    """
    Parse a display-style offering tag like ``25-26Fall`` or ``24-25Spring``.

    Returns:
        Tuple of (anchor_year, semester_code), where anchor_year is 4-digit.
    """
    if not isinstance(tag_name, str):
        return None

    match = re.match(r'^\s*(\d{2})-(\d{2})(Spring|Summer|Fall|Winter|春|夏|秋|冬)\s*$', tag_name)
    if not match:
        return None

    start_year_2d, end_year_2d, season_part = match.groups()
    anchor_year = f"20{start_year_2d}"
    expected_end_year = f"{(int(anchor_year) + 1) % 100:02d}"
    if end_year_2d != expected_end_year:
        return None

    semester_code = normalize_semester_code(season_part)
    if not semester_code:
        return None

    return anchor_year, semester_code


def normalize_semester_code(input_semester: str) -> Optional[str]:
    """
    Normalize various semester inputs to standard codes.
    
    Args:
        input_semester: Input like "春", "Spring", "fall", "2024秋", etc.
    
    Returns:
        Standard semester code or None if invalid
    """
    if not input_semester:
        return None
        
    # Clean input
    clean_input = input_semester.strip()
    
    # If it contains a year, extract just the season part
    match = re.match(r'^\d{2,4}(.+)$', clean_input)
    if match:
        clean_input = match.group(1)
    
    # Check direct mapping
    if clean_input in CHINESE_TO_CODE:
        return CHINESE_TO_CODE[clean_input]
    
    # Check lowercase standard codes
    lower_input = clean_input.lower()
    if lower_input in [SemesterCode.SPRING, SemesterCode.SUMMER, SemesterCode.FALL, SemesterCode.WINTER]:
        return lower_input
    
    # Check common variations
    variations = {
        'spr': SemesterCode.SPRING,
        'spring': SemesterCode.SPRING,
        'sum': SemesterCode.SUMMER, 
        'summer': SemesterCode.SUMMER,
        'fal': SemesterCode.FALL,
        'fall': SemesterCode.FALL,
        'autumn': SemesterCode.FALL,
        'win': SemesterCode.WINTER,
        'winter': SemesterCode.WINTER
    }
    
    return variations.get(lower_input)


def normalize_offering_identifier(value: str) -> Optional[Tuple[str, str]]:
    """
    Normalize either an internal semester key (``2025fall``) or a display tag
    (``25-26Fall``) into ``(anchor_year, semester_code)``.
    """
    if not value or not isinstance(value, str):
        return None

    parsed_display = parse_offering_display_tag(value)
    if parsed_display:
        return parsed_display

    match = re.match(r'^\s*(\d{4})(spring|summer|fall|winter)\s*$', value, re.IGNORECASE)
    if match:
        year, season = match.groups()
        return year, season.lower()

    return None

def sort_semesters(semesters: List[str], parse_year: bool = True) -> List[str]:
    """
    Sort semesters by year and season order.
    
    Args:
        semesters: List of semester strings (either tags or just codes)
        parse_year: Whether to parse year from semester strings
    
    Returns:
        Sorted list of semesters (newest first)
    """
    def get_sort_key(semester: str) -> Tuple[int, int]:
        if parse_year:
            # Try to parse as semester tag first
            parsed = parse_semester_tag(semester)
            if parsed:
                _, year_str, season_code = parsed
                year = int(year_str)
                season_order = SEMESTER_ORDER.get(season_code, 0)
                return (-year, -season_order)

            normalized_offering = normalize_offering_identifier(semester)
            if normalized_offering:
                year_str, season_code = normalized_offering
                year = int(year_str)
                season_order = SEMESTER_ORDER.get(season_code, 0)
                return (-year, -season_order)
        
        # Try to extract year from string
        year_match = re.search(r'(\d{4})', semester)
        if year_match:
            year = int(year_match.group(1))
        else:
            year = 0
            
        # Get season order
        season_code = normalize_semester_code(semester)
        season_order = SEMESTER_ORDER.get(season_code, 0)
        
        return (-year, -season_order)
    
    return sorted(semesters, key=get_sort_key)

def is_valid_semester_format(year: str, semester_code: str) -> bool:
    """
    Validate year and semester code format.
    
    Args:
        year: Year string (e.g., "2024")
        semester_code: Standard semester code
    
    Returns:
        True if valid format
    """
    try:
        year_int = int(year)
        return (1900 <= year_int <= 2100 and 
                semester_code in [SemesterCode.SPRING, SemesterCode.SUMMER, 
                                 SemesterCode.FALL, SemesterCode.WINTER])
    except ValueError:
        return False

def find_matching_semester_tag(course_code: str, year: str, semester_code: str, all_tags: list) -> Optional[str]:
    """
    Find a matching semester tag from a list, trying different formats.
    
    Args:
        course_code: Course code (e.g., "AIAA 1010")
        year: Year string (e.g., "2024")
        semester_code: Standard semester code (e.g., "fall")
        all_tags: List of tag objects with 'name' attribute
    
    Returns:
        Matching tag object or None
    """
    tag_names = [tag.name for tag in all_tags]
    
    # Try different possible formats
    possible_formats = [
        # Legacy format: "AIAA 1010-24Fall"
        format_semester_tag(course_code, year, semester_code, use_legacy_format=True),
        # New format: "AIAA 1010-2024fall"
        format_semester_tag(course_code, year, semester_code, use_legacy_format=False),
        # Other variations
        f"{course_code}-{year[-2:]}{semester_code}",  # "AIAA 1010-24fall"
        f"{course_code}-{year}{semester_code.capitalize()}",  # "AIAA 1010-2024Fall"
    ]
    
    for tag_format in possible_formats:
        for tag in all_tags:
            if tag.name == tag_format:
                return tag
    
    return None

def normalize_semester_tag_format(tag_name: str) -> Optional[Tuple[str, str, str]]:
    """
    Normalize a semester tag to standard format, handling various input formats.
    
    Args:
        tag_name: Tag name in any supported format
    
    Returns:
        Tuple of (course_code, normalized_year, normalized_season) or None
    """
    parsed = parse_semester_tag(tag_name)
    if not parsed:
        return None
    
    course_code, year, semester_code = parsed
    
    # Normalize year to 4-digit format
    if len(year) == 2:
        year_prefix = "20" if int(year) < 50 else "19"
        normalized_year = year_prefix + year
    else:
        normalized_year = year
    
    # Normalize semester to lowercase standard code
    normalized_season = normalize_semester_code(semester_code) or semester_code.lower()
    
    return course_code, normalized_year, normalized_season


def infer_offering_from_datetime(dt: datetime) -> Tuple[str, str]:
    """
    Infer an offering from a post timestamp for backfilling historical reviews.

    Rules:
    - Feb-Jun  -> Spring of the academic year that started the previous calendar year
    - Jul-Aug  -> Summer of the academic year that started the previous calendar year
    - Sep-Dec  -> Fall of the academic year that starts in the current calendar year
    - Jan      -> Fall of the academic year that started in the previous calendar year
    """
    month = dt.month
    year = dt.year

    if 2 <= month <= 6:
        return str(year - 1), SemesterCode.SPRING
    if 7 <= month <= 8:
        return str(year - 1), SemesterCode.SUMMER
    if month == 1:
        return str(year - 1), SemesterCode.FALL
    return str(year), SemesterCode.FALL
