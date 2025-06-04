import json
import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.course import Course
from app.models.tag import TagType
from app import create_app, db

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

def import_courses_from_file(file_path, session):
    """Import courses from a JSON file."""
    print(f"Processing file: {file_path}")
    
    # Get term code from filename (e.g., courses_2410.json -> 2410)
    term_code = Path(file_path).stem.split('_')[1]
    season = get_term_season(term_code)
    
    with open(file_path, 'r', encoding='utf-8') as f:
        courses_data = json.load(f)
    
    # Get or create COURSE tag type
    course_tag_type = session.query(TagType).filter_by(name='COURSE').first()
    if not course_tag_type:
        course_tag_type = TagType(name='COURSE')
        session.add(course_tag_type)
        session.commit()
    
    imported_count = 0
    skipped_count = 0
    
    for course_data in courses_data:
        try:
            # Skip if required fields are missing
            if not all(k in course_data for k in ['course_code', 'name', 'unit']):
                print(f"Skipping course due to missing required fields: {course_data}")
                skipped_count += 1
                continue
            
            # Clean and prepare course data
            code = course_data['course_code'].strip()
            name = course_data['name'].strip()
            credits = int(course_data['unit'])
            
            # Skip if any required field is empty
            if not code or not name:
                print(f"Skipping course due to empty required fields: {course_data}")
                skipped_count += 1
                continue
            
            # Check if course already exists
            existing_course = session.query(Course).filter_by(code=code).first()
            if existing_course:
                # Update existing course
                existing_course.name = name
                existing_course.credits = credits
                course = existing_course
            else:
                # Create new course
                course = Course(
                    code=code,
                    name=name,
                    credits=credits,
                    is_active=True
                )
                session.add(course)
                session.flush()  # Get the course ID
            
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