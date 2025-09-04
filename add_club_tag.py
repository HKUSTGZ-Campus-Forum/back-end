#!/usr/bin/env python3
"""
Script to add 'club' tag to posts by post ID.

Usage:
    python add_club_tag.py <post_id> [post_id ...]
    python add_club_tag.py 1 2 3 5

This script will:
1. Create the 'club' tag if it doesn't exist (as a system tag)
2. Add the club tag to the specified posts
3. Skip posts that already have the club tag
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from app.extensions import db
from app.models.post import Post
from app.models.tag import Tag, TagType
from sqlalchemy import or_


def ensure_club_tag_exists():
    """Ensure the 'club' tag exists, create it if not."""
    club_tag = Tag.query.filter_by(name='club').first()
    
    if not club_tag:
        # Get system tag type
        system_tag_type = TagType.get_system_type()
        if not system_tag_type:
            # Create system tag type if it doesn't exist
            system_tag_type = TagType(name=TagType.SYSTEM)
            db.session.add(system_tag_type)
            db.session.commit()
        
        # Create club tag
        club_tag = Tag(
            name='club',
            tag_type_id=system_tag_type.id,
            description='Posts related to club activities and student organizations'
        )
        db.session.add(club_tag)
        db.session.commit()
        print(f"✅ Created 'club' tag (ID: {club_tag.id})")
    else:
        print(f"✅ 'club' tag already exists (ID: {club_tag.id})")
    
    return club_tag


def add_club_tag_to_post(post_id, club_tag):
    """Add club tag to a specific post."""
    # Find the post
    post = Post.query.filter_by(id=post_id, is_deleted=False).first()
    
    if not post:
        print(f"❌ Post with ID {post_id} not found or is deleted")
        return False
    
    # Check if post already has club tag
    if club_tag in post.tags:
        print(f"⚠️  Post {post_id} already has 'club' tag")
        return True
    
    # Add the club tag
    post.tags.append(club_tag)
    db.session.commit()
    print(f"✅ Added 'club' tag to post {post_id}: '{post.title[:50]}...'")
    return True


def main():
    """Main function to process command line arguments."""
    if len(sys.argv) < 2:
        print("Usage: python add_club_tag.py <post_id> [post_id ...]")
        print("Example: python add_club_tag.py 1 2 3 5")
        sys.exit(1)
    
    # Parse post IDs
    post_ids = []
    for arg in sys.argv[1:]:
        try:
            post_id = int(arg)
            post_ids.append(post_id)
        except ValueError:
            print(f"❌ Invalid post ID: {arg} (must be an integer)")
            sys.exit(1)
    
    if not post_ids:
        print("❌ No valid post IDs provided")
        sys.exit(1)
    
    print(f"🎯 Processing {len(post_ids)} post(s): {post_ids}")
    print("-" * 50)
    
    # Create Flask app context
    app = create_app()
    
    with app.app_context():
        try:
            # Ensure club tag exists
            club_tag = ensure_club_tag_exists()
            print("-" * 50)
            
            # Process each post
            success_count = 0
            for post_id in post_ids:
                if add_club_tag_to_post(post_id, club_tag):
                    success_count += 1
            
            print("-" * 50)
            print(f"✅ Successfully processed {success_count}/{len(post_ids)} posts")
            
            if success_count < len(post_ids):
                print(f"⚠️  {len(post_ids) - success_count} posts could not be processed")
        
        except Exception as e:
            print(f"❌ Error: {e}")
            db.session.rollback()
            sys.exit(1)


def interactive_mode():
    """Interactive mode for adding club tags."""
    app = create_app()
    
    with app.app_context():
        try:
            # Ensure club tag exists
            club_tag = ensure_club_tag_exists()
            print("-" * 50)
            
            while True:
                # Show recent posts that don't have club tag
                print("\n📋 Recent posts without 'club' tag:")
                recent_posts = Post.query.filter(
                    ~Post.tags.any(Tag.name == 'club'),
                    Post.is_deleted == False
                ).order_by(Post.created_at.desc()).limit(10).all()
                
                if not recent_posts:
                    print("No posts found without 'club' tag")
                    break
                
                for i, post in enumerate(recent_posts, 1):
                    print(f"{i:2d}. [ID:{post.id:3d}] {post.title[:60]}...")
                
                print("\n" + "=" * 70)
                user_input = input("\nEnter post ID to tag (or 'q' to quit, 'list' to refresh): ").strip()
                
                if user_input.lower() in ['q', 'quit', 'exit']:
                    break
                elif user_input.lower() in ['list', 'refresh', 'l']:
                    continue
                
                try:
                    post_id = int(user_input)
                    add_club_tag_to_post(post_id, club_tag)
                except ValueError:
                    print(f"❌ Invalid input: {user_input}")
                except KeyboardInterrupt:
                    print("\n👋 Goodbye!")
                    break
        
        except Exception as e:
            print(f"❌ Error: {e}")
            db.session.rollback()


if __name__ == "__main__":
    if len(sys.argv) == 1:
        print("🎯 Starting interactive mode...")
        print("You can add 'club' tags to posts interactively.")
        print("=" * 50)
        interactive_mode()
    else:
        main()