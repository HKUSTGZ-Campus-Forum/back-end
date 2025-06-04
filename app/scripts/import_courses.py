import json
import os
from pathlib import Path
from sqlalchemy.exc import IntegrityError
from app import create_app, db
from app.models.course import Course
from app.models.tag import Tag, TagType

def ensure_tag_type(app):
    """Ensure the course tag type exists in the database."""
    with app.app_context():
        course_type = TagType.get_course_type()
        if not course_type:
            course_type = TagType(name=TagType.COURSE)
            db.session.add(course_type)
            db.session.commit()
            print("Created course tag type")
        return course_type

def get_semester_name(term_code):
    """Convert term code to semester name."""
    year = term_code[:2]
    term = term_code[2:]
    term_map = {
        '10': 'Fall',
        '30': 'Spring',
        '40': 'Summer'
    }
    semester = term_map.get(term)
    if not semester:
        raise ValueError(f"Invalid term code: {term_code}")
    return f"{year}{semester}"

def validate_course_data(course_data):
    """Validate course data before import."""
    required_fields = ['course_code', 'name', 'unit']
    for field in required_fields:
        if field not in course_data:
            raise ValueError(f"Missing required field: {field}")
    
    if not isinstance(course_data['unit'], (int, float)) or course_data['unit'] < 0:
        raise ValueError(f"Invalid unit value: {course_data['unit']}")
    
    if not course_data['course_code'].strip():
        raise ValueError("Course code cannot be empty")

def import_courses_from_file(file_path, app):
    """Import courses from a JSON file."""
    with open(file_path, 'r', encoding='utf-8') as f:
        courses_data = json.load(f)
    
    # Get the term code from the filename (e.g., courses_2440.json -> 2440)
    term_code = Path(file_path).stem.split('_')[1]
    try:
        semester_name = get_semester_name(term_code)
    except ValueError as e:
        print(f"Error processing file {file_path}: {str(e)}")
        return 0, 0, 0
    
    # Ensure course tag type exists
    ensure_tag_type(app)
    
    # Import courses
    with app.app_context():
        imported = 0
        skipped = 0
        errors = 0
        
        for course_data in courses_data:
            try:
                # Validate course data
                validate_course_data(course_data)
                
                # Check if course already exists
                existing_course = Course.query.filter_by(
                    code=course_data['course_code']
                ).first()
                
                if existing_course:
                    # If course exists, just create the semester tag
                    try:
                        existing_course.create_semester_tag(semester_name)
                        skipped += 1
                        continue
                    except IntegrityError:
                        # Tag might already exist, which is fine
                        skipped += 1
                        continue
                
                # Create new course
                course = Course(
                    code=course_data['course_code'],
                    name=course_data['name'],
                    credits=course_data['unit'],
                    description=f"Course offered in {semester_name} semester"
                )
                
                db.session.add(course)
                db.session.flush()  # Get the course ID
                
                # Create semester tag for the course
                try:
                    course.create_semester_tag(semester_name)
                    imported += 1
                except IntegrityError:
                    # Tag might already exist, which is fine
                    imported += 1
                
            except ValueError as e:
                db.session.rollback()
                errors += 1
                print(f"Error importing course {course_data.get('course_code', 'UNKNOWN')}: {str(e)}")
            except IntegrityError as e:
                # This is actually a duplicate course, not an error
                db.session.rollback()
                skipped += 1
                # Only print if it's not a duplicate tag (which is expected)
                if "duplicate key value violates unique constraint" not in str(e).lower():
                    print(f"Note: Course {course_data.get('course_code', 'UNKNOWN')} already exists")
            except Exception as e:
                db.session.rollback()
                errors += 1
                print(f"Error importing course {course_data.get('course_code', 'UNKNOWN')}: {str(e)}")
        
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Error committing changes: {str(e)}")
            return 0, 0, len(courses_data)  # Mark all as errors if commit fails
            
        return imported, skipped, errors

def import_all_courses():
    """Import courses from all JSON files in the course directory."""
    app = create_app()
    
    # Get the course directory path
    course_dir = Path(__file__).parent.parent.parent.parent / 'course'
    
    # Find all course JSON files
    course_files = list(course_dir.glob('courses_*.json'))
    
    if not course_files:
        print("No course JSON files found!")
        return
    
    total_imported = 0
    total_skipped = 0
    total_errors = 0
    
    print(f"Found {len(course_files)} course files to import:")
    for file_path in course_files:
        print(f"\nProcessing {file_path.name}...")
        imported, skipped, errors = import_courses_from_file(file_path, app)
        
        total_imported += imported
        total_skipped += skipped
        total_errors += errors
        
        print(f"Results for {file_path.name}:")
        print(f"  - Imported: {imported}")
        print(f"  - Skipped (already exist): {skipped}")
        print(f"  - Errors: {errors}")
    
    print("\nImport Summary:")
    print(f"Total courses imported: {total_imported}")
    print(f"Total courses skipped: {total_skipped}")
    print(f"Total errors: {total_errors}")

if __name__ == '__main__':
    import_all_courses() 