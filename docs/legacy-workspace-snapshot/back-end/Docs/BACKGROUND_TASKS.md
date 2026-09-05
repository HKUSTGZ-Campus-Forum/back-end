# Background Task System Documentation

## Overview

The campus forum backend uses a unified background task system built on APScheduler to handle periodic maintenance tasks. This system is designed for high reliability, extensibility, and consistent with the project's architecture patterns.

## Architecture

### Core Components

1. **Unified Scheduler** (`app/tasks/sts_pool.py`)
   - Single APScheduler instance managing all background tasks
   - Automatically starts when Flask app initializes
   - Manages both existing and new background tasks

2. **Embedding Maintenance** (`app/tasks/embedding_maintenance.py`)
   - Auto-recovery for missing profile/project embeddings
   - Configurable batch processing with time limits
   - Comprehensive error handling and statistics tracking

3. **Embedding Service** (`app/services/embedding_service.py`)
   - Centralized embedding generation for multiple use cases
   - Extensible for future AI/ML features beyond matching
   - Built-in caching and performance optimization

4. **Management API** (`app/routes/background_tasks.py`)
   - Monitor task status and performance
   - Force-run tasks for urgent maintenance
   - Health check endpoints for system monitoring

## Key Design Decisions

### 1. Unified vs Separate Schedulers
**Decision**: Use single APScheduler instance for all tasks
**Rationale**:
- Reduces resource overhead
- Consistent with existing codebase patterns
- Easier to monitor and manage
- Simpler deployment and configuration

### 2. Task Location: `app/tasks/` vs `app/services/`
**Decision**: Background tasks in `app/tasks/`, reusable services in `app/services/`
**Rationale**:
- Follows existing project structure (`app/tasks/sts_pool.py`)
- Clear separation of concerns
- `app/tasks/` = scheduled/periodic tasks
- `app/services/` = reusable business logic

### 3. Embedding Auto-Recovery Strategy
**Decision**: Batch processing with time limits and graceful degradation
**Rationale**:
- Prevents system overload during peak usage
- Ensures system responsiveness
- Handles large backlogs efficiently
- Configurable performance tuning

## Configuration

### Environment Variables

```bash
# Background Task Control
ENABLE_BACKGROUND_TASKS=true                    # Enable/disable entire system
EMBEDDING_MAINTENANCE_INTERVAL_MINUTES=60       # How often to run embedding maintenance
EMBEDDING_MAINTENANCE_BATCH_SIZE=50             # Records to process per batch
EMBEDDING_MAINTENANCE_MAX_TIME_MINUTES=30       # Max time per maintenance run

# Embedding Service (existing)
DASHSCOPE_API_KEY=your_api_key
DASHVECTOR_API_KEY=your_vector_db_key
DASHVECTOR_ENDPOINT=your_endpoint
```

### Default Behavior
- **STS Pool Maintenance**: Every 15 minutes (existing)
- **Embedding Maintenance**: Every 60 minutes (new)
- **Batch Size**: 50 records per run (configurable)
- **Time Limit**: 30 minutes max per run (configurable)

## Usage Guide

### Starting the System

The background task system starts automatically when the Flask app initializes:

```python
# In app/__init__.py
from app.tasks.sts_pool import init_pool_maintenance

def create_app():
    # ... other initialization
    init_pool_maintenance(app)  # Starts unified scheduler with all tasks
```

### Monitoring Tasks

#### 1. API Endpoints

```bash
# Get overall status
GET /api/background-tasks/status

# Health check (no auth required)
GET /api/background-tasks/health

# Force run embedding maintenance
POST /api/background-tasks/embedding-maintenance/run
{
  "target_type": "all",     # "profiles", "projects", or "all"
  "batch_size": 100         # Max 500 for safety
}
```

#### 2. Example Response

```json
{
  "success": true,
  "scheduler": {
    "running": true,
    "jobs": [
      {
        "id": "sts_pool_maintenance",
        "name": "sts_pool_maintenance",
        "next_run": "2025-09-26T15:30:00",
        "trigger": "interval[0:15:00]"
      },
      {
        "id": "embedding_maintenance",
        "name": "embedding_maintenance",
        "next_run": "2025-09-26T16:00:00",
        "trigger": "interval[1:00:00]"
      }
    ]
  },
  "embedding_maintenance": {
    "stats": {
      "last_run": "2025-09-26T15:00:00",
      "total_profiles_fixed": 42,
      "total_projects_fixed": 18,
      "total_errors": 0
    }
  }
}
```

### Adding New Background Tasks

To add a new background task, follow this pattern:

#### 1. Create Task Function

```python
# In app/tasks/your_new_task.py
def init_your_task(app, scheduler):
    """Initialize your background task"""
    scheduler.add_job(
        id='your_task_name',
        func=_your_task_job,
        args=[app],
        trigger='interval',
        minutes=30,  # Your interval
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60
    )

def _your_task_job(app):
    """Task implementation with app context"""
    with app.app_context():
        try:
            current_app.logger.info("Running your task...")
            # Your task logic here
            db.session.commit()
        except Exception as e:
            current_app.logger.error(f"Error in your task: {e}")
            db.session.rollback()
```

#### 2. Register Task

```python
# In app/tasks/sts_pool.py, add to init_pool_maintenance():
try:
    from app.tasks.your_new_task import init_your_task
    init_your_task(app, unified_scheduler)
except Exception as e:
    app.logger.warning(f"Could not initialize your task: {e}")
```

## Important Implementation Notes

### 1. Database Transactions
- **Always use app context**: `with app.app_context():`
- **Commit explicitly**: `db.session.commit()` after successful operations
- **Rollback on errors**: `db.session.rollback()` in exception handlers
- **Flush for IDs**: Use `db.session.flush()` when you need IDs before commit

### 2. Error Handling
- **Non-blocking failures**: Tasks should not crash the entire scheduler
- **Comprehensive logging**: Use `current_app.logger` for consistent formatting
- **Graceful degradation**: Continue processing even if some records fail
- **Statistics tracking**: Keep counts of successes/failures for monitoring

### 3. Performance Considerations
- **Batch processing**: Process records in configurable batches
- **Time limits**: Respect maximum execution time to avoid blocking
- **Resource limits**: Limit API calls and database operations per run
- **Graceful shutdown**: Handle scheduler shutdown cleanly

### 4. Testing Background Tasks

```python
# Example test pattern
def test_your_background_task():
    with app.app_context():
        # Setup test data
        # Call task function directly (not via scheduler)
        result = _your_task_job(app)
        # Assert results
```

## Troubleshooting

### Common Issues

#### 1. Tasks Not Running
- Check `ENABLE_BACKGROUND_TASKS=true` in environment
- Verify scheduler is started: check `/api/background-tasks/health`
- Look for initialization errors in application logs

#### 2. High Memory Usage
- Reduce `EMBEDDING_MAINTENANCE_BATCH_SIZE`
- Decrease `EMBEDDING_MAINTENANCE_MAX_TIME_MINUTES`
- Monitor with `/api/background-tasks/status`

#### 3. Embedding Generation Failures
- Verify `DASHSCOPE_API_KEY` is valid
- Check `DASHVECTOR_*` configuration
- Monitor API rate limits
- Review embedding service logs

#### 4. Database Lock Issues
- Ensure tasks use separate transactions
- Avoid long-running operations
- Use `db.session.flush()` appropriately

### Monitoring and Alerting

#### Key Metrics to Monitor
- **Scheduler Status**: Should always be `running: true`
- **Job Count**: Should have expected number of registered jobs
- **Error Rates**: Monitor `total_errors` in embedding stats
- **Processing Rates**: Track `total_*_fixed` to ensure progress
- **Response Times**: API endpoints should respond quickly

#### Log Patterns to Watch
```bash
# Successful task completion
"✅ Embedding maintenance completed: profiles_fixed=2, projects_fixed=1"

# Error patterns
"❌ Error processing profile 123: connection timeout"
"❌ Failed to generate embedding for project 456"

# Performance warnings
"Time limit reached, stopping profile processing"
```

## Future Extensions

### Planned Features
1. **Content Classification**: Use embeddings for automatic content categorization
2. **Recommendation Engine**: Generate personalized content recommendations
3. **Semantic Search**: Enhance search functionality with vector similarity
4. **Analytics Aggregation**: Background processing of user analytics
5. **Cache Maintenance**: Automatic cleanup of expired cache entries

### Extension Points
- **`app/services/embedding_service.py`**: Add new use cases in `model_configs`
- **`app/tasks/`**: Add new task modules following existing patterns
- **API endpoints**: Extend monitoring and control capabilities
- **Configuration**: Add new environment variables for tuning

### Embedding Service Use Cases
The embedding service is designed for extensibility:

```python
# Current use cases
embedding_service.generate_embedding(text, use_case="matching")   # User/project matching
embedding_service.generate_embedding(text, use_case="search")     # Semantic search
embedding_service.generate_embedding(text, use_case="content")    # Content analysis

# Future use cases (easy to add)
embedding_service.generate_embedding(text, use_case="recommendation")  # Recommendations
embedding_service.generate_embedding(text, use_case="classification")  # Auto-categorization
embedding_service.generate_embedding(text, use_case="similarity")      # Content similarity
```

## Security Considerations

### API Access Control
- Background task APIs require JWT authentication
- Consider adding admin-only access for sensitive operations
- Health check endpoint is public (no auth required)

### Data Privacy
- Embeddings are generated from user content
- Ensure compliance with data privacy regulations
- Consider data retention policies for embedding vectors

### Rate Limiting
- Background tasks respect API rate limits
- Configurable batch sizes prevent overwhelming external services
- Time limits ensure fair resource usage

## Deployment Notes

### Production Deployment
1. **Environment Variables**: Ensure all required config is set
2. **Database Migrations**: Run before deploying new background tasks
3. **Monitoring Setup**: Configure alerting for task failures
4. **Resource Allocation**: Ensure sufficient memory/CPU for background processing
5. **Graceful Shutdown**: Tasks handle application restarts properly

### Development
1. **Local Testing**: Background tasks can be disabled with `ENABLE_BACKGROUND_TASKS=false`
2. **Force Running**: Use API endpoints to test tasks manually
3. **Debugging**: Tasks log extensively for troubleshooting

This background task system is designed to scale with your platform's growth while maintaining reliability and performance. The unified architecture makes it easy to add new AI/ML features as your platform evolves.