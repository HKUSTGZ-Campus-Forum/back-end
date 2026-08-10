import json
import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.course import Course
from app.models.tag import TagType
from app import create_app, db
from app.services.course_domain import normalize_course_code


class CourseImportValidationError(RuntimeError):
    pass

def get_term_season(term_code):
    """Convert term code to season name."""
    season_map = {
        '10': 'Fall',
        '30': 'Spring',
        '40': 'Summer'
    }
    year = term_code[:2]
    season_code = term_code[2:]
    return f"{year}{season_map.get(season_code, '')}"


def _prepare_course_rows(courses_data):
    prepared_rows = []
    skipped_count = 0
    source_codes_by_normalized = {}

    for course_data in courses_data:
        try:
            if not isinstance(course_data, dict) or not all(
                key in course_data for key in ['course_code', 'name', 'unit']
            ):
                print(f"Skipping course due to missing required fields: {course_data}")
                skipped_count += 1
                continue

            code = course_data['course_code'].strip()
            name = course_data['name'].strip()
            credits = int(course_data['unit'])
            normalized_code = normalize_course_code(code)
            if not normalized_code or not name:
                print(f"Skipping course due to empty required fields: {course_data}")
                skipped_count += 1
                continue
        except Exception as error:
            print(f"Error processing course {course_data}: {str(error)}")
            skipped_count += 1
            continue

        previous_code = source_codes_by_normalized.get(normalized_code)
        if previous_code is not None:
            raise CourseImportValidationError(
                'input contains duplicate normalized course identity '
                f'{normalized_code}: {previous_code!r}, {code!r}'
            )
        source_codes_by_normalized[normalized_code] = code
        prepared_rows.append((course_data, code, normalized_code, name, credits))

    return prepared_rows, skipped_count


def _existing_courses_by_normalized_code(session, normalized_codes):
    normalized_codes = set(normalized_codes)
    identity_rows = session.query(
        Course.id,
        Course.code,
        Course.normalized_code,
    ).all()
    candidate_ids_by_code = {}
    for course_id, code, normalized_code in identity_rows:
        stored_code = normalize_course_code(normalized_code)
        derived_code = normalize_course_code(code)
        if not ({stored_code, derived_code} & normalized_codes):
            continue
        if stored_code and stored_code != derived_code:
            raise CourseImportValidationError(
                'course normalization is inconsistent for existing row '
                f'id={course_id}: code={code!r}, normalized_code={normalized_code!r}'
            )
        candidate_code = stored_code or derived_code
        candidate_ids_by_code.setdefault(candidate_code, []).append(course_id)

    ambiguous = {
        normalized_code: matching_ids
        for normalized_code, matching_ids in candidate_ids_by_code.items()
        if len(matching_ids) > 1
    }
    if ambiguous:
        details = '; '.join(
            f'{normalized_code}=rows[{",".join(str(course_id) for course_id in matching_ids)}]'
            for normalized_code, matching_ids in sorted(ambiguous.items())
        )
        raise CourseImportValidationError(
            'ambiguous existing course rows for normalized import codes: ' + details
        )

    candidate_ids = [
        matching_ids[0]
        for matching_ids in candidate_ids_by_code.values()
    ]
    candidates_by_id = {
        course.id: course
        for course in session.query(Course).filter(Course.id.in_(candidate_ids)).all()
    } if candidate_ids else {}
    return {
        normalized_code: candidates_by_id[matching_ids[0]]
        for normalized_code, matching_ids in candidate_ids_by_code.items()
    }

def import_courses_from_file(file_path, session):
    """Import courses from a JSON file."""
    print(f"Processing file: {file_path}")
    
    # Get term code from filename (e.g., courses_2410.json -> 2410)
    term_code = Path(file_path).stem.split('_')[1]
    season = get_term_season(term_code)
    
    with open(file_path, 'r', encoding='utf-8') as f:
        courses_data = json.load(f)

    if not isinstance(courses_data, list):
        raise CourseImportValidationError('course import payload must be a list')

    prepared_rows, skipped_count = _prepare_course_rows(courses_data)
    courses_by_normalized_code = _existing_courses_by_normalized_code(
        session,
        [row[2] for row in prepared_rows],
    )
    
    # Get or create COURSE tag type
    course_tag_type = session.query(TagType).filter_by(name='COURSE').first()
    if not course_tag_type:
        course_tag_type = TagType(name='COURSE')
        session.add(course_tag_type)
        session.commit()
    
    imported_count = 0
    
    for course_data, code, normalized_code, name, credits in prepared_rows:
        try:
            existing_course = courses_by_normalized_code.get(normalized_code)
            if existing_course:
                # Update existing course
                existing_course.name = name
                existing_course.credits = credits
                existing_course.normalized_code = normalized_code
                course = existing_course
            else:
                # Create new course
                course = Course(
                    code=normalized_code,
                    normalized_code=normalized_code,
                    name=name,
                    credits=credits,
                    is_active=True
                )
                session.add(course)
                session.flush()  # Get the course ID
                courses_by_normalized_code[normalized_code] = course
            
            # Create semester tag using the Course model's method
            try:
                course.create_semester_tag(season)
            except Exception as e:
                print(f"Error creating semester tag for course {code}: {str(e)}")
                session.rollback()
                skipped_count += 1
                continue
            
            imported_count += 1
            
            # Commit every 100 courses to avoid large transactions
            if imported_count % 100 == 0:
                session.commit()
                print(f"Imported {imported_count} courses so far...")
        
        except Exception as e:
            print(f"Error processing course {course_data}: {str(e)}")
            session.rollback()
            skipped_count += 1
            continue
    
    # Final commit
    session.commit()
    print(f"\nImport completed for {file_path}")
    print(f"Successfully imported: {imported_count}")
    print(f"Skipped: {skipped_count}")
    return imported_count, skipped_count

def main():
    """Main function to import all course files."""
    # Create Flask app context
    app = create_app()
    with app.app_context():
        # Get the database session
        session = db.session
        
        # Directory containing course JSON files
        course_dir = Path(__file__).parent.parent.parent.parent / 'course'
        
        # Find all course JSON files
        course_files = list(course_dir.glob('courses_*.json'))
        
        if not course_files:
            print("No course files found!")
            return
        
        total_imported = 0
        total_skipped = 0
        
        # Process each file
        for file_path in course_files:
            imported, skipped = import_courses_from_file(file_path, session)
            total_imported += imported
            total_skipped += skipped
        
        print("\nImport Summary:")
        print(f"Total files processed: {len(course_files)}")
        print(f"Total courses imported: {total_imported}")
        print(f"Total courses skipped: {total_skipped}")

if __name__ == '__main__':
    main()
