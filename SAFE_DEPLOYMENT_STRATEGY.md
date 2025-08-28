# Safe Identity System Deployment Strategy

## Problem
- Migration folders have diverged between local, dev, and prod
- Cannot safely push new migration files
- Need to deploy identity system without breaking existing migrations

## Solution: Migration-Free Deployment

### Step 1: Enhanced init_db.py Approach
Instead of using migrations, we'll make `init_db.py` handle ALL the database structure changes.

### Step 2: What to Push
- ✅ All model files (identity_type.py, user_identity.py)  
- ✅ Updated existing models (post.py, comment.py, gugu_message.py)
- ✅ Route files (identity.py)
- ✅ Enhanced init_db.py
- ❌ NO migration files

### Step 3: Deployment Commands
The GitHub workflow will run:
1. `flask db migrate` - This will generate a new migration locally on server
2. `flask db upgrade` - This will apply the auto-generated migration
3. `python -m app.scripts.init_db` - This will populate identity types

### Step 4: If Auto-Migration Fails
Have manual SQL ready as backup:

```sql
-- Create identity_types table
CREATE TABLE IF NOT EXISTS identity_types (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    color VARCHAR(7) DEFAULT '#2563eb' NOT NULL,
    icon_name VARCHAR(50),
    description TEXT,
    is_active BOOLEAN DEFAULT true NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL
);

-- Create user_identities table  
CREATE TABLE IF NOT EXISTS user_identities (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    identity_type_id INTEGER NOT NULL REFERENCES identity_types(id),
    status VARCHAR(20) DEFAULT 'pending' NOT NULL,
    verification_documents JSONB,
    verified_by INTEGER REFERENCES users(id),
    rejection_reason TEXT,
    notes TEXT,
    verified_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    UNIQUE(user_id, identity_type_id),
    CHECK (status IN ('pending', 'approved', 'rejected', 'revoked'))
);

-- Add display_identity_id columns
ALTER TABLE posts ADD COLUMN IF NOT EXISTS display_identity_id INTEGER REFERENCES user_identities(id);
ALTER TABLE comments ADD COLUMN IF NOT EXISTS display_identity_id INTEGER REFERENCES user_identities(id);  
ALTER TABLE gugu_messages ADD COLUMN IF NOT EXISTS display_identity_id INTEGER REFERENCES user_identities(id);

-- Insert identity types
INSERT INTO identity_types (name, display_name, color, icon_name, description) VALUES
('professor', 'Professor', '#dc2626', 'academic-cap', 'University professor or teaching staff'),
('staff', 'Staff Member', '#059669', 'user-group', 'University administrative or support staff'),
('officer', 'School Officer', '#7c3aed', 'shield-check', 'Student government or official school organization officer'),
('student_leader', 'Student Leader', '#ea580c', 'star', 'Student club president or community leader')
ON CONFLICT (name) DO NOTHING;

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_user_identities_status ON user_identities(status);
CREATE INDEX IF NOT EXISTS idx_user_identities_user_status ON user_identities(user_id, status);
```

## Execution Plan

### Option A: Trust GitHub Workflow (Recommended)
1. Don't push migration files
2. Push only the code changes  
3. Let server auto-generate migration
4. Monitor deployment logs

### Option B: Manual Deployment (Safest)
1. SSH to server
2. Pull code changes
3. Skip `flask db migrate` 
4. Run manual SQL above
5. Run init_db.py
6. Restart service

Both approaches avoid the migration conflict issue entirely.