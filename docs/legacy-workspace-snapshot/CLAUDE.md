## Notes
- main folders are frontend and backend, others are less important and might be outdated
- back-end and front-end have different .git folder and is managed seperately, they're not sharing same repo in github as well.
- both backend and frontend is deployed on remote server, thus local codebase may less some files/folders (e.g. migration, .env)
- there's a github workflow for both frontend and backend, handling auto deploy to dev address when push on main.
- backend server rely on database, thus deployed only remotely; while frontend could be run locally visiting the dev address api endpoints

## School-production CI rollout note (2026-08-29)

- A formal `main` -> dev deployment -> `school-production` PR -> school server deployment pipeline has been prepared from the latest backend `origin/main` (`5b112411eef490842d608b86ac333b1360a53d7f`) and checked against the latest frontend `origin/main` (`40b3afd3a7210de388e72ec4a7075f0e42aa6a34`). The exact-SHA backend and frontend dev deployment runs were successful, and the complete backend suite passed (`929 passed, 6 skipped`).
- The CI change limits the production control branch to a reviewed release manifest, validates that both candidate SHAs already deployed successfully to dev, serializes releases until the prior deployment is terminal, automates school-server activation, and supports fixed, commit-defined backend operation requests (including dry-run/apply approval and sanitized CI-visible receipts) without exposing a general remote shell or SQL interface.
- This pipeline is **prepared and tested, but not live yet**. It becomes operational only after the backend CI changes are merged, the root-owned school controller is reinstalled once interactively, required GitHub branch protection/status checks are configured, and the first production PR completes with an exact public deployment receipt. Until those rollout steps are complete, do not treat a merge to `school-production` as proof of deployment.

## Recent Development Log (2025-09-14 - Latest: Teammate Matching System)

### 🤝 Teammate Matching & Project Collaboration Platform (September 2025)
**Branch**: `main` (integrated)
**Major Feature**: Complete teammate matching and project collaboration system

**Overview**: Implemented a comprehensive platform for finding teammates and joining projects using semantic search and compatibility scoring.

**New Database Models**:
- **`user_profiles`**: Extended user profiles with skills, interests, experience level, and semantic embeddings
- **`projects`**: Project proposals with requirements, team size, and collaboration details
- **`project_applications`**: Applications linking users to projects with match scores and status tracking

**Core Features**:
1. **Profile System**: Rich user profiles with skills, interests, experience level, and availability
2. **Project Creation**: Flexible project posting (from detailed plans to simple keywords)
3. **Semantic Matching**: AI-powered recommendations using DashScope embeddings + DashVector search
4. **Compatibility Scoring**: Multi-factor scoring algorithm:
   - Skills matching (30%): Required vs available skills
   - Experience alignment (15%): User level vs project difficulty
   - Role preferences (25%): Preferred vs needed roles
   - Availability compatibility (15%): Time commitment alignment
   - Interest alignment (15%): Semantic similarity of interests
5. **Application System**: Apply to projects, track status, team formation

**Technical Implementation**:
```python
# Matching service with embedding integration
class MatchingService:
    def find_project_matches(user_id, limit=10)  # User → Projects
    def find_teammate_matches(project_id, limit=10)  # Project → Users
    def _calculate_compatibility_score(profile, project)  # Multi-factor scoring
```

**New API Endpoints**:
- `/api/profiles/` - CRUD for user profiles
- `/api/projects/` - CRUD for projects
- `/api/matching/projects` - Get project recommendations
- `/api/matching/teammates/<project_id>` - Get teammate recommendations
- `/api/matching/applications` - Application management
- `/api/matching/dashboard` - Unified dashboard data

**Frontend Pages & Components**:
- `/matching/` - Main dashboard with stats and activity
- `/matching/profile` - Profile setup/editing with skill selector
- `/matching/discover` - Project discovery with semantic recommendations
- **Components**: ProjectCard, SkillSelector, ApplicationModal

**Key Technical Features**:
- **Embedding Generation**: Uses DashScope text-embedding-v4 (1024 dimensions)
- **Vector Search**: DashVector with cosine similarity for semantic matching
- **Smart Caching**: Embedding vectors cached for performance
- **Flexible Input**: Projects can be detailed plans or simple keywords/ideas
- **Real-time Updates**: Live application status, team formation tracking

**User Experience Flow**:
1. **Setup**: Profile completion prompt → Skill/interest selection → Experience level
2. **Discovery**: Personalized project recommendations → Filter/browse → Apply with message
3. **Management**: Track applications → Manage projects → Team formation

**Database Migration**:
Created migration script: `app/scripts/create_matching_tables.py`

**Expected Impact**:
- Easier team formation for student projects
- Better skill-project matching through AI
- Reduced friction in finding collaborators
- Enhanced campus collaboration ecosystem

---

## Previous Development Log (2025-01-10)

### 1. Course Comments Branch - SQL & Tag System Fixes
**Branch**: `course-comments`
**Issues Fixed**:
- **SQL DuplicateAlias Error**: Fixed duplicate `Tag` joins in course posts query (`app/routes/course.py:get_course_posts`)
- **Semester Tag Validation**: Implemented flexible semester tag matching to handle format differences
  - Database: `"AIAA 1010-24Fall"` vs Frontend: `"AIAA 1010-2024fall"`
  - Created `app/utils/semester.py` with standardized semester utilities
  - Added `find_matching_semester_tag()` function for flexible validation

**Key Technical Changes**:
```python
# Fixed SQL query in course.py
posts = Post.query.join(Post.tags).filter(Tag.name.in_(semester_tags)).all()
# Removed redundant .join(Tag) that caused DuplicateAlias error

# Added flexible tag validation in post.py
matching_tag = find_matching_semester_tag(course_code, year, semester_code, all_course_tags)
```

### 2. Daily Summary & Analytics System
**Branch**: `daily-summary`
**New Features**:
- **Hot Score Algorithm**: `(reactions×3 + comments×5 + views×0.1) / age_hours^0.8`
- **Social Media Text Generation**: Chinese marketing content for platform promotion
- **Real-time Hot Posts**: Main page dashboard with 5-minute auto-refresh
- **Comprehensive Analytics**: Daily engagement metrics, trending topics

**Key Files Created/Modified**:
- `app/routes/analytics.py`: New analytics endpoints with hot score calculation
- `front-end/pages/index.vue`: Transformed from empty page to engaging dashboard
- Algorithm balances engagement vs recency for optimal content discovery

### 3. Public Access Implementation
**Purpose**: Attract new users by allowing guest access to read-only content
**Changes Made**:
- **Backend**: Removed `@jwt_required()` from read endpoints (analytics, post details)
- **Frontend**: Updated main page to use `fetchPublic()` instead of `fetchWithAuth()`
- **Security**: Write operations still require authentication

**Affected Endpoints**:
- `/api/analytics/hot-posts` - Now public
- `/api/analytics/daily-summary` - Now public  
- `/api/posts/<id>` (GET) - Already public
- `/api/posts` (GET) - Already public

### 4. Semester System Standardization
**Problem**: Mixed Chinese/English semester codes causing validation failures
**Solution**: Standardized semester codes with multi-language support
- **Standard Codes**: `spring`, `summer`, `fall`, `winter` (instead of 春/夏/秋/冬)
- **Display Names**: Localized for UI presentation
- **Backward Compatibility**: Handles existing Chinese format tags in database

**Technical Implementation**:
```python
# app/utils/semester.py
SEMESTER_MAPPINGS = {
    'spring': {'zh': '春', 'en': 'Spring'},
    'summer': {'zh': '夏', 'en': 'Summer'},
    'fall': {'zh': '秋', 'en': 'Fall'},
    'winter': {'zh': '冬', 'en': 'Winter'}
}
```

### 5. Current Branch Status
- **main**: Contains all stable features
- **course-comments**: ✅ Merged - Course comment functionality working
- **daily-summary**: ✅ Merged - Analytics and dashboard complete
- **Current**: Back on `course-comments` branch for continued development

### 6. Database Schema Notes
- **TagType**: Already exists and properly initialized (course, user, system)
- **Semester Tags**: Format `"COURSE_CODE-YYSemester"` (e.g., "AIAA 1010-24Fall")
- **Tag Validation**: Course tags require existing Course record and valid semester

### 7. Frontend Architecture Notes
- **API Calls**: Always use `fetchWithAuth()` for authenticated endpoints, `fetchPublic()` for guest access
- **Hot Posts**: Auto-refresh every 5 minutes on main page
- **Responsive Design**: CSS Grid layout with mobile optimization
- **Real-time Updates**: Dashboard shows live engagement metrics

### 8. Avatar System Redesign (January 2025)
**Problem**: Avatar images disappearing after page refresh due to expired signed URLs being stored in database
**Root Cause**: Old system stored signed URLs directly in `profile_picture_url` field, while post images generated fresh URLs each time
**Solution**: Redesigned avatar system to use File references like post images

**Key Changes**:
- **Backend**: Added `profile_picture_file_id` field referencing File records
- **Backend**: Created `avatar_url` property that generates fresh signed URLs each time
- **Frontend**: Updated `AvatarUpload.vue` to save file IDs instead of URLs
- **Database**: Migration script adds new column and index

**Technical Implementation**:
```python
# New User model structure
profile_picture_file_id = db.Column(db.Integer, db.ForeignKey('files.id'), nullable=True)
profile_picture_file = db.relationship('File', foreign_keys=[profile_picture_file_id], post_update=True, uselist=False)

@property
def avatar_url(self):
    """Generate fresh signed URL each time"""
    if self.profile_picture_file_id and self.profile_picture_file:
        return self.profile_picture_file.url  # Fresh signed URL
    return None
```

**Frontend Changes**:
```typescript
// Updated avatar upload to use file_id
await updateUserProfile({
  profile_picture_file_id: uploadResult.id  // Save file ID, not URL
});
```

**Benefits**:
- ✅ Avatars persist after page refresh
- ✅ Fresh signed URLs generated each time
- ✅ Consistent with post image system
- ✅ No manual URL expiration handling needed

### 9. Redis Caching System Implementation (August 2025)
**Problem**: High OSS traffic and slow image loading due to generating fresh signed URLs for every file access
**Root Cause**: `File.url` property generated new signed URLs on every access, causing excessive OSS API calls
**Solution**: Implemented comprehensive Redis-based caching system

**Key Changes**:
- **Backend**: Added Redis caching infrastructure with Flask-Caching
- **File URLs**: Smart caching with 45-minute cache duration and 4-hour URL expiry
- **Cache Management**: Full cache service with statistics, warming, and maintenance
- **Multi-Environment**: Separate Redis databases for dev (db=1) and production (db=0)

**Technical Implementation**:
```python
# Enhanced File.url property with Redis caching
@property
def url(self):
    cache_key = f"file_url:{self.id}"
    cached_url = cache.get(cache_key)
    if cached_url:
        return cached_url  # Cache hit - no OSS call
    
    # Generate signed URL with 4-hour duration (increased from 1 hour)
    signed_url = bucket.sign_url("GET", self.object_name, 14400)
    cache.set(cache_key, signed_url, timeout=2700)  # Cache for 45 minutes
    return signed_url
```

**New Files Created**:
- `app/services/cache_service.py`: Cache management utilities
- `app/routes/cache.py`: Admin cache management endpoints
- `test_redis_cache.py`: Comprehensive testing script

**Configuration Added**:
```python
# Redis Configuration
REDIS_URL = 'redis://localhost:6379/0'  # Production: db=0, Dev: db=1
FILE_URL_CACHE_TIMEOUT = 2700  # 45 minutes
FILE_URL_CACHE_KEY_PREFIX = 'file_url:'
```

**Expected Performance Improvements**:
- **90%+ reduction** in OSS API calls
- **50-80% faster** image loading times
- **Significant cost savings** on OSS bandwidth
- **Better user experience** with instant image loads

**Cache Management Features**:
- **Statistics**: Memory usage, hit ratios, cache entry counts
- **Cache Warming**: Pre-generate URLs for improved performance
- **Auto-refresh**: Background refresh of expiring URLs
- **Manual Control**: Clear specific or all cached URLs

**Monitoring & Debugging**:
```bash
# Check cache status
redis-cli -n 1 KEYS file_url:*  # Dev environment
redis-cli -n 0 KEYS file_url:*  # Production environment

# Monitor cache operations
redis-cli MONITOR

# Admin endpoints
GET /api/admin/cache/stats
POST /api/admin/cache/clear
POST /api/admin/cache/warm
```

**Multi-Environment Setup**:
- **Production**: Uses Redis database 0 (`REDIS_URL=redis://localhost:6379/0`)
- **Development**: Uses Redis database 1 (`REDIS_URL=redis://localhost:6379/1`)
- **Isolation**: Complete separation between environments
- **Shared Instance**: Single Redis process for efficiency

### 10. Deployment & Testing
- **Dev Environment**: https://dev.unikorn.axfff.com/api
- **Auto Deploy**: GitHub workflow deploys on main branch push
- **Testing**: Course comments working, avatar system fully functional
- **Performance**: Hot score algorithm optimized for real-time calculation

## Background Task System & Embedding Service (September 2025)
**Branch**: `main` (integrated)
**Major Enhancement**: Unified background task system with auto-recovery for embedding issues

**Problem Solved**: Duplicate search results caused by inconsistent embedding generation
- Some profiles/projects missing embeddings → disappeared from search
- Inconsistent embedding generation (with/without project context) → duplicate results
- No automatic recovery when embedding generation failed

**Solution Implemented**:
1. **Unified Background Task System** (`app/tasks/`)
   - Integrated with existing APScheduler infrastructure (`sts_pool.py`)
   - Auto-recovery for missing profile/project embeddings
   - Configurable batch processing with performance limits
   - Comprehensive error handling and monitoring

2. **Centralized Embedding Service** (`app/services/embedding_service.py`)
   - Extensible for future AI/ML features beyond matching
   - Multiple use cases: matching, search, content analysis
   - Intelligent caching (7-day TTL) for performance
   - Consistent embedding generation across all features

3. **Cleanup & Migration Tools** (`app/scripts/cleanup_duplicate_vectors.py`)
   - One-time script to fix existing duplicate vectors
   - Safe to run multiple times
   - Regenerates all embeddings with consistent formatting

**Key Configuration**:
```bash
ENABLE_BACKGROUND_TASKS=true                    # Control system
EMBEDDING_MAINTENANCE_INTERVAL_MINUTES=60       # How often to run
EMBEDDING_MAINTENANCE_BATCH_SIZE=50             # Performance tuning
EMBEDDING_MAINTENANCE_MAX_TIME_MINUTES=30       # Resource limits
```

**API Endpoints Added**:
- `GET /api/background-tasks/status` - Monitor task health
- `GET /api/background-tasks/health` - Public health check
- `POST /api/background-tasks/embedding-maintenance/run` - Force maintenance

**Documentation**:
- `docs/BACKGROUND_TASKS.md` - Comprehensive system documentation
- `docs/EMBEDDING_SERVICE.md` - Service architecture and extensibility
- `docs/QUICK_REFERENCE.md` - Deployment and troubleshooting guide

**Deployment Steps**:
1. Deploy code changes
2. Run `python app/scripts/cleanup_duplicate_vectors.py` ONCE
3. Verify background tasks with health check endpoint
4. Monitor embedding maintenance statistics

**Future Extensibility**:
The embedding service is designed for easy extension to new AI/ML features:
- Content recommendations
- Automatic classification
- Enhanced search capabilities
- User behavior analytics

## When developing
- note information you feel necessary (e.g. code design, third-party api note, special/temp logic, etc.) for futher human/AI developer.
- **Background Tasks**: Add new tasks to `app/tasks/` following the unified scheduler pattern
- **Embedding Features**: Use `embedding_service.generate_embedding(text, use_case="your_feature")` for new AI/ML capabilities
- **Performance**: Monitor embedding maintenance stats and tune batch sizes based on server capacity
