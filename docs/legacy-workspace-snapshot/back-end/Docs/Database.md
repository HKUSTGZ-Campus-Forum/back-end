# Database Structure

This document describes the database structure for the campus forum. The system includes tables for users, posts, comments (with sub-comments), reactions (emoji reactions instead of likes), tags, a sign-up calendar. posts and comments have embedding vectors for semantic search.

## 1. Users Table

Stores information about the users of the forum.

```sql
CREATE TABLE UserRoles (
    role_id SERIAL PRIMARY KEY,
    role_name VARCHAR(50) UNIQUE NOT NULL
);

CREATE TABLE Users (
    user_id SERIAL PRIMARY KEY,
    username VARCHAR(50) NOT NULL,
    password_hash TEXT NOT NULL,
    email VARCHAR(100),
    email_verified BOOLEAN DEFAULT FALSE NOT NULL,
    phone_number VARCHAR(20),
    phone_verified BOOLEAN DEFAULT FALSE NOT NULL,
    profile_picture_url TEXT,
    role_id INT NOT NULL,
    is_deleted BOOLEAN DEFAULT FALSE NOT NULL,
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    FOREIGN KEY (role_id) REFERENCES UserRoles(role_id),
    CONSTRAINT valid_email CHECK (email ~* '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$'),
    CONSTRAINT valid_phone CHECK (phone_number ~ '^\+?[0-9]{10,15}$'),
    CONSTRAINT unique_active_username UNIQUE (username) WHERE NOT is_deleted
);

CREATE INDEX idx_users_username ON Users(username) WHERE NOT is_deleted;
CREATE INDEX idx_users_email ON Users(email) WHERE NOT is_deleted;
```

## 2. Token Blacklist Table

Stores revoked JWT tokens to implement secure logout functionality and prevent the use of invalid tokens.

```sql
CREATE TABLE token_blacklist (
    id SERIAL PRIMARY KEY,
    jti VARCHAR(36) UNIQUE NOT NULL,
    token_type VARCHAR(10) NOT NULL,
    user_id INT NOT NULL,
    revoked_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    expires TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (user_id) REFERENCES Users(user_id) ON DELETE CASCADE
);

CREATE INDEX idx_token_blacklist_jti ON token_blacklist(jti);
CREATE INDEX idx_token_blacklist_user_id ON token_blacklist(user_id);
CREATE INDEX idx_token_blacklist_expires ON token_blacklist(expires);
```

## 3. Posts Table

Stores posts created by users. Includes a reference to the user who created the post.

```sql
CREATE TABLE Posts (
    post_id SERIAL PRIMARY KEY,
    user_id INT NOT NULL,
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    embedding JSON,
    comment_count INT DEFAULT 0 NOT NULL,
    reaction_count INT DEFAULT 0 NOT NULL,
    views_count INT DEFAULT 0 NOT NULL,
    is_deleted BOOLEAN DEFAULT FALSE NOT NULL,
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    FOREIGN KEY (user_id) REFERENCES Users(user_id) ON DELETE CASCADE,
    CONSTRAINT valid_counts CHECK (comment_count >= 0 AND reaction_count >= 0 AND views_count >= 0)
);

CREATE INDEX idx_posts_user_id ON Posts(user_id) WHERE NOT is_deleted;
CREATE INDEX idx_posts_created_at ON Posts(created_at) WHERE NOT is_deleted;
```

## 4. Comments Table

Stores comments on posts. Supports sub-comments (recursive structure via `parent_comment_id`).

```sql
CREATE TABLE Comments (
    comment_id SERIAL PRIMARY KEY,
    post_id INT NOT NULL,
    user_id INT NOT NULL,
    content TEXT NOT NULL,
    embedding JSON,
    parent_comment_id INT,
    is_deleted BOOLEAN DEFAULT FALSE NOT NULL,
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (post_id) REFERENCES Posts(post_id),
    FOREIGN KEY (user_id) REFERENCES Users(user_id),
    FOREIGN KEY (parent_comment_id) REFERENCES Comments(comment_id)
);

CREATE INDEX idx_comments_post_id ON Comments(post_id) WHERE NOT is_deleted;
```

## 5. Reaction System Tables

### 5.1 Reaction Emojis Table

Stores the available emoji options for reactions, including custom emojis.

```sql
CREATE TABLE ReactionEmojis (
    emoji_id SERIAL PRIMARY KEY,
    emoji_code VARCHAR(50) NOT NULL,
    description TEXT,
    image_url TEXT,
    display_order INT DEFAULT 0 NOT NULL,
    is_active BOOLEAN DEFAULT TRUE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT unique_emoji_code UNIQUE (emoji_code)
);

CREATE INDEX idx_reaction_emojis_display_order ON ReactionEmojis(display_order) WHERE is_active;
```

### 5.2 Reactions Table

Stores emoji reactions for posts and comments, referencing the ReactionEmojis table.

```sql
CREATE TABLE Reactions (
    reaction_id SERIAL PRIMARY KEY,
    user_id INT NOT NULL,
    post_id INT,
    comment_id INT,
    emoji_id INT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    FOREIGN KEY (user_id) REFERENCES Users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (post_id) REFERENCES Posts(post_id) ON DELETE CASCADE,
    FOREIGN KEY (comment_id) REFERENCES Comments(comment_id) ON DELETE CASCADE,
    FOREIGN KEY (emoji_id) REFERENCES ReactionEmojis(emoji_id) ON DELETE RESTRICT,
    CONSTRAINT unique_reaction UNIQUE (user_id, post_id, comment_id, emoji_id),
    CONSTRAINT valid_target CHECK (
        (post_id IS NULL AND comment_id IS NOT NULL) OR
        (post_id IS NOT NULL AND comment_id IS NULL)
    )
);

CREATE INDEX idx_reactions_post_id ON Reactions(post_id);
CREATE INDEX idx_reactions_comment_id ON Reactions(comment_id);
CREATE INDEX idx_reactions_user_id ON Reactions(user_id);
```

## 6. Calendar System Tables

### 6.1 Calendar Emojis Table

Stores the available emoji options for calendar marks.

```sql
CREATE TABLE CalendarEmojis (
    emoji_id SERIAL PRIMARY KEY,
    emoji_code VARCHAR(50) NOT NULL,
    description TEXT,
    image_url TEXT,
    display_order INT DEFAULT 0 NOT NULL,
    probability DECIMAL(5,4) DEFAULT 0.0 NOT NULL,
    is_active BOOLEAN DEFAULT TRUE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT valid_probability CHECK (probability >= 0.0 AND probability <= 1.0),
    CONSTRAINT unique_emoji_code UNIQUE (emoji_code)
);

CREATE INDEX idx_calendar_emojis_display_order ON CalendarEmojis(display_order) WHERE is_active;
```

### 6.2 User Calendar Table

```sql
CREATE TABLE UserCalendar (
    calendar_id SERIAL PRIMARY KEY,
    user_id INT NOT NULL,
    emoji_id INT NOT NULL,
    date DATE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    FOREIGN KEY (user_id) REFERENCES Users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (emoji_id) REFERENCES CalendarEmojis(emoji_id) ON DELETE RESTRICT,
    CONSTRAINT unique_status_per_day UNIQUE (user_id, date)
);

CREATE INDEX idx_user_calendar_date ON UserCalendar(date);
CREATE INDEX idx_user_calendar_user_id ON UserCalendar(user_id);
```

## 7. Tags Table

Stores tags, which can be system-defined or user-defined. Tags are associated with posts for categorization.

```sql
CREATE TABLE Tags (
    tag_id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    tag_type ENUM('system', 'user') DEFAULT 'user',
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
```

## 8. PostTags Table (Many-to-Many Relationship between Posts and Tags)

Links posts to tags. Each post can have multiple tags.

```sql
CREATE TABLE PostTags (
    post_id INT NOT NULL,
    tag_id INT NOT NULL,
    PRIMARY KEY (post_id, tag_id),
    FOREIGN KEY (post_id) REFERENCES Posts(post_id),
    FOREIGN KEY (tag_id) REFERENCES Tags(tag_id)
);
```

## 9. Embedding for Posts and Comments (Optional)

The `embedding` field stores vector embeddings for posts and comments, enabling semantic search and clustering. The embeddings will be computed by a third-party service and stored in the database.

## Summary of Relationships

- **Users to Posts**: One-to-many (one user can create many posts).
- **Posts to Comments**: One-to-many (one post can have many comments).
- **Users to Comments**: One-to-many (one user can make many comments).
- **Users to Reactions**: One-to-many (one user can react with emojis to posts or comments).
- **Posts to Tags**: Many-to-many (a post can have multiple tags, and tags can be applied to multiple posts).
- **Comments to Sub-Comments**: Recursive (a comment can have multiple sub-comments, which are linked to the parent comment via `parent_comment_id`).
- **Calendar**: Each user has a calendar with daily status (active, absent, etc.).
- **Reactions System**: Custom emojis stored in ReactionEmojis table, with reactions linking to specific emojis
- **Calendar System**: Custom emojis stored in CalendarEmojis table, with calendar entries linking to specific emojis

This design allows to manage user posts, comments, reactions, tags, a sign-up calendar, and embedding vectors efficiently while supporting advanced features like semantic search and auto-clustering based on vector embeddings.