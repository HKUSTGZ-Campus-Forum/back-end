# Campus Forum Codebase Documentation

**Project**: UniKorn Campus Forum
**Description**: A comprehensive campus forum platform with teammate matching, project collaboration, and social features
**Architecture**: Frontend (Nuxt.js/Vue.js) + Backend (Flask/Python)
**Generated**: September 2025

## 🏗️ Overall Architecture

### System Overview
```
┌─────────────────┐    HTTP/API   ┌──────────────────┐
│   Frontend      │ ◄──────────►  │    Backend       │
│   (Nuxt 3)      │               │    (Flask)       │
│   Port: 3000    │               │    Port: 5000    │
└─────────────────┘               └──────────────────┘
                                           │
                                           ▼
                    ┌─────────────────────────────────────┐
                    │          Infrastructure             │
                    │  • PostgreSQL (Database)            │
                    │  • Redis (Caching)                  │
                    │  • Alibaba Cloud OSS (Files)        │
                    │  • DashVector (Vector Search)       │
                    └─────────────────────────────────────┘
```

### Key Features
- **Forum System**: Posts, comments, reactions, course discussions
- **Authentication**: JWT-based with OAuth integration
- **File Management**: Cloud storage with signed URLs
- **Team Matching**: AI-powered teammate discovery and project collaboration
- **Real-time Features**: Notifications, hot post tracking
- **Multi-language**: Chinese/English support
- **Mobile-responsive**: PWA capabilities

---

## Backend Architecture (Flask/Python)

### Project Structure
```
back-end/
├── app/
│   ├── __init__.py           # Flask app factory
│   ├── config.py             # Configuration settings
│   ├── extensions.py         # Flask extensions init
│   ├── models/               # Database models (SQLAlchemy)
│   ├── routes/               # API endpoints (Blueprints)
│   ├── services/             # Business logic layer
│   ├── tasks/                # Background tasks (APScheduler)
│   ├── utils/                # Helper utilities
│   └── scripts/              # Database migration scripts
├── tests/                    # Unit and integration tests
├── requirements.txt          # Python dependencies
└── run.py                   # Application entry point
```

### Database Models (SQLAlchemy ORM)

#### Core Models
- **`User`**: User accounts with authentication, roles, and profiles
- **`Post`**: Forum posts with content, attachments, and metadata
- **`Comment`**: Nested comments with threading support
- **`Course`**: Academic courses with semester-based organization
- **`Tag`**: Flexible tagging system (course, user, system tags)
- **`File`**: Cloud file storage with signed URL generation

#### Team Matching Models
- **`UserProfile`**: Extended user profiles with skills, interests, and embeddings
- **`Project`**: Project proposals with requirements and team information
- **`ProjectApplication`**: Applications linking users to projects with status tracking

#### Additional Models
- **`Reaction`**: Post/comment reactions with custom emojis
- **`Notification`**: Push notifications with delivery tracking
- **`UserCalendar`**: Academic calendar integration
- **`OAuthClient`**: OAuth2 server implementation

### API Routes (Flask Blueprints)

#### Authentication & Users
- **`auth.py`**: Login, logout, token refresh, password management
- **`user.py`**: User CRUD operations, profile management
- **`identity.py`**: Student identity verification system

#### Content Management
- **`post.py`**: Forum posts with CRUD, reactions, file attachments
- **`comment.py`**: Comment threads with nested replies
- **`file.py`**: Cloud file upload/download with OSS integration
- **`course.py`**: Course management and semester tagging

#### Advanced Features
- **`matching.py`**: AI-powered teammate and project matching
- **`profile.py`**: Enhanced user profiles for matching
- **`project.py`**: Project collaboration management
- **`analytics.py`**: Hot posts algorithm and engagement metrics
- **`notification.py`**: Push notification system
- **`search.py`**: Global search across posts and courses

#### System Management
- **`cache.py`**: Redis cache management and statistics
- **`background_tasks.py`**: Task monitoring and maintenance
- **`oauth.py`**: OAuth2 server implementation

### Services Layer

#### Core Services
- **`file_service.py`**: Alibaba Cloud OSS integration with STS tokens
- **`embedding_service.py`**: AI embeddings for semantic search (DashScope)
- **`matching_service.py`**: Teammate compatibility algorithms
- **`cache_service.py`**: Redis caching with smart invalidation
- **`notification_service.py`**: Push notification delivery

#### Background Processing
- **`sts_pool.py`**: STS token pool management for OSS
- **`embedding_maintenance.py`**: Automated embedding generation and cleanup

### Key Technical Features

#### Authentication System
```python
# JWT-based authentication with refresh tokens
@jwt_required()
def protected_route():
    current_user = get_jwt_identity()
    return jsonify(user_id=current_user)
```

#### File Upload Flow
```python
# Generate signed URL for direct OSS upload
signed_url = file_service.generate_upload_url(filename, content_type)
# Frontend uploads directly to OSS
# Backend receives callback and creates File record
```

#### AI-Powered Matching
```python
# Semantic similarity using embeddings
compatibility_score = matching_service.calculate_compatibility(
    user_profile, project_requirements
)
```

#### Smart Caching
```python
# Redis-based caching with automatic expiration
@cached(timeout=3600, key_prefix='user_profile')
def get_user_profile(user_id):
    return User.query.get(user_id)
```

---

## Frontend Architecture (Nuxt.js/Vue.js)

### Project Structure
```
front-end/
├── components/              # Reusable Vue components
│   ├── auth/               # Authentication components
│   ├── forum/              # Forum-related components
│   ├── home/               # Homepage navigation
│   ├── common/             # Shared UI components
│   └── matching/           # Team matching UI
├── pages/                  # File-based routing (Nuxt)
│   ├── index.vue           # Homepage dashboard
│   ├── forum/              # Forum pages
│   ├── matching/           # Teammate matching pages
│   ├── login/              # Authentication pages
│   └── admin/              # Administration interface
├── composables/            # Reusable composition functions
├── store/                  # Pinia state management
├── assets/                 # Static assets and styles
├── public/                 # Public static files
├── nuxt.config.ts         # Nuxt configuration
└── package.json           # Dependencies and scripts
```

### Component Architecture

#### Navigation System
```
HomeContainer.vue              # Main layout wrapper
├── HomePinned.vue            # Top navigation bar
└── HomeSidebar.vue           # Left sidebar navigation
```

#### Forum Components
```
forum/
├── Post.vue                  # Individual post display
├── PostMessage.vue           # Post creation/editing
├── Comment.vue               # Comment display
├── CommentList.vue           # Comment threading
└── CommentForm.vue           # Comment creation
```

#### Authentication Components
```
auth/
├── Login.vue                 # Login form
├── Register.vue              # Registration form
└── UserAvatar.vue            # Smart avatar with refresh logic
```

### State Management (Pinia)

#### Core Stores
- **Auth Store**: User authentication state, token management
- **User Store**: User profile data and caching
- **Forum Store**: Posts, comments, and forum state
- **Theme Store**: 6-theme system with CSS custom properties

### API Integration

#### Composables
```typescript
// useApi.ts - Centralized API client with auth
const { fetchWithAuth } = useApi()
const response = await fetchWithAuth('/api/posts')

// useAuth.ts - Authentication state management
const { login, logout, user, isAuthenticated } = useAuth()

// useFileUpload.ts - File upload to OSS
const { uploadFile, uploadProgress } = useFileUpload()
```

#### Authentication Flow
```typescript
// Automatic token refresh with interceptors
if (isTokenExpired(token)) {
    await refreshToken()
}
```

### Theme System

#### 6-Theme Architecture
- **Light**: Default bright theme
- **Dark**: Dark mode for low-light use
- **Cafe**: Warm brown tones
- **Pro-Tech**: Professional blue/gray
- **Ocean**: Blue-green palette
- **Sunset**: Warm orange/pink

#### CSS Custom Properties
```scss
// Theme-aware component styling
.component {
  background: var(--surface-primary);
  color: var(--text-primary);
  border: 1px solid var(--border-primary);
}
```

### Responsive Design

#### Mobile-First Approach
- **CSS Grid/Flexbox**: Responsive layouts
- **PWA Support**: Installable web app
- **Touch Optimization**: Mobile gesture support

---

## Key Features Deep Dive

### 1. AI-Powered Team Matching System

#### Architecture
```
User Profile → Embedding Generation → Vector Search → Compatibility Scoring
```

#### Components
- **Backend**: DashScope embeddings + DashVector search
- **Algorithm**: Multi-factor compatibility scoring (skills, experience, interests)
- **Frontend**: Swipe-based discovery interface

### 2. Advanced File Management

#### OSS Integration Flow
```
1. Frontend requests signed URL from backend
2. Backend generates STS token and signed URL
3. Frontend uploads directly to Alibaba Cloud OSS
4. OSS sends callback to backend
5. Backend creates File record and generates viewing URLs
```

#### Smart Caching
- **45-minute URL cache**: Reduces OSS API calls by 90%
- **Auto-refresh**: Background refresh of expiring URLs
- **Fallback handling**: Graceful degradation for expired URLs

### 3. Real-time Analytics

#### Hot Posts Algorithm
```python
hot_score = (reactions×3 + comments×5 + views×0.1) / age_hours^0.8
```

#### Features
- **5-minute refresh**: Real-time hot post updates
- **Engagement tracking**: Views, reactions, comment metrics
- **Trending analysis**: Daily/weekly trending topics

### 4. Comprehensive Authentication

#### Security Features
- **JWT tokens**: Stateless authentication
- **Automatic refresh**: Seamless token renewal
- **OAuth2 server**: Third-party integration support
- **Identity verification**: Student ID verification system

---

## Development & Deployment

### Development Setup

#### Backend
```bash
cd back-end
pip install -r requirements.txt
python run.py
```

#### Frontend
```bash
cd front-end
npm install
npm run dev
```

### Environment Configuration

#### Backend (.env)
```bash
FLASK_ENV=development
DATABASE_URL=postgresql://user:pass@localhost/db
REDIS_URL=redis://localhost:6379/1
OSS_ACCESS_KEY_ID=your_key
OSS_ACCESS_KEY_SECRET=your_secret
```

#### Frontend (.env)
```bash
NUXT_PUBLIC_API_BASE_URL=https://dev.unikorn.axfff.com
```

### Deployment Strategy

#### Production Architecture
- **Backend**: Gunicorn + Nginx
- **Frontend**: Static generation + CDN
- **Database**: PostgreSQL with connection pooling
- **Caching**: Redis cluster
- **Storage**: Alibaba Cloud OSS
- **CI/CD**: GitHub Actions with automatic deployment

---

## Security & Performance

### Security Measures
- **Input validation**: Bleach for HTML sanitization
- **SQL injection prevention**: SQLAlchemy ORM
- **XSS protection**: Content Security Policy
- **File upload security**: MIME type validation, virus scanning
- **Rate limiting**: API request throttling
- **Secrets management**: Environment variables, no hardcoded credentials

### Performance Optimizations
- **Database indexing**: Optimized queries with proper indexes
- **Caching strategy**: Multi-level caching (Redis, browser, CDN)
- **File optimization**: Image compression, lazy loading
- **Code splitting**: Dynamic imports for reduced bundle size
- **Background processing**: Async task processing with APScheduler

---

## Database Schema Overview

### Core Tables
```sql
users               # User accounts and authentication
user_profiles       # Extended profiles for matching
posts               # Forum posts with content
comments            # Threaded comments
files               # Cloud file storage references
courses             # Academic course catalog
tags                # Flexible tagging system
reactions           # Post/comment reactions
notifications       # Push notification queue
```

### Relationships
- **Users ↔ Posts**: One-to-many with author relationship
- **Posts ↔ Comments**: One-to-many with threading
- **Posts ↔ Files**: Many-to-many for attachments
- **Users ↔ Projects**: Many-to-many through applications
- **Posts ↔ Tags**: Many-to-many for categorization

---

## 🚀 Recent Major Updates (2025)

### Team Matching System (September 2025)
- AI-powered semantic matching with DashScope embeddings
- Multi-factor compatibility scoring algorithm
- Project collaboration and team formation features
- Enhanced user profiles with skills and interests

### Redis Caching Implementation (August 2025)
- Smart file URL caching with 45-minute duration
- 90% reduction in OSS API calls
- Background URL refresh for seamless user experience

### Avatar System Overhaul (January 2025)
- Smart avatar refresh with expiration detection
- Automatic URL refresh for expired signed URLs
- Graceful fallback to initials for failed loads

---

## 🔮 Future Roadmap

### Planned Features
- **Real-time messaging**: WebSocket-based chat system
- **Mobile app**: React Native companion app
- **Advanced analytics**: User behavior tracking and insights
- **Content moderation**: AI-powered content filtering
- **Gamification**: User reputation and achievement systems

### Technical Improvements
- **Microservices**: Split monolith into specialized services
- **GraphQL**: Replace REST with GraphQL for flexible queries
- **Kubernetes**: Container orchestration for scalability
- **Machine learning**: Enhanced recommendation algorithms

---

## 🤝 Contributing

### Development Guidelines
1. **Code Style**: Follow PEP 8 (Python) and Vue.js style guide
2. **Testing**: Write unit tests for new features
3. **Documentation**: Update documentation for API changes
4. **Security**: Never commit secrets or credentials
5. **Performance**: Consider caching and optimization impact

### Key Development Patterns
- **Backend**: Service layer pattern, dependency injection
- **Frontend**: Composition API, reactive programming
- **Database**: Migration-based schema changes
- **API**: RESTful design with consistent error handling

---

**Last Updated**: September 29, 2025
**Documentation Maintainers**: Development Team
**Contact**: [Project Repository Issues](https://github.com/your-org/campus-forum/issues)