# Quick Reference Guide

## 🚀 Deployment Checklist

### After Deploying Embedding System Updates

1. **Run Cleanup Script (ONE TIME ONLY)**
   ```bash
   cd /path/to/campusForum/back-end
   python app/scripts/cleanup_duplicate_vectors.py
   ```
   - Fixes duplicate search results
   - Regenerates all embeddings with consistent formatting
   - Safe to run multiple times

2. **Verify Background Tasks Started**
   ```bash
   curl https://your-domain.com/api/background-tasks/health
   ```
   Expected response: `{"healthy": true}`

3. **Monitor Task Status**
   ```bash
   curl -H "Authorization: Bearer YOUR_JWT" https://your-domain.com/api/background-tasks/status
   ```

## 🔧 Common Operations

### Check Embedding System Health
```bash
# Health check (no auth required)
GET /api/background-tasks/health

# Detailed status (requires auth)
GET /api/background-tasks/status
```

### Force Run Embedding Maintenance
```bash
# Fix all missing embeddings
POST /api/background-tasks/embedding-maintenance/run
Content-Type: application/json
Authorization: Bearer YOUR_JWT

{
  "target_type": "all",
  "batch_size": 100
}
```

### Monitor Specific Issues
```bash
# Check for profiles without embeddings
SELECT COUNT(*) FROM user_profiles WHERE embedding IS NULL AND is_active = true;

# Check for projects without embeddings
SELECT COUNT(*) FROM projects WHERE embedding IS NULL AND is_deleted = false;
```

## 🐛 Troubleshooting

### Issue: Background Tasks Not Running
```bash
# Check if scheduler is running
curl /api/background-tasks/health

# Look for errors in logs
grep -i "scheduler\|embedding\|error" /path/to/logs/app.log
```

**Solution**: Verify `ENABLE_BACKGROUND_TASKS=true` in environment

### Issue: High Memory Usage
```bash
# Reduce batch size
export EMBEDDING_MAINTENANCE_BATCH_SIZE=25
export EMBEDDING_MAINTENANCE_MAX_TIME_MINUTES=15
```

### Issue: API Rate Limits
```bash
# Check DashScope quotas
# Reduce batch size or increase interval
export EMBEDDING_MAINTENANCE_INTERVAL_MINUTES=120
```

### Issue: Duplicate Search Results Still Appearing
1. Run cleanup script again: `python app/scripts/cleanup_duplicate_vectors.py`
2. Check logs for embedding generation errors
3. Verify all profiles/projects have embeddings

## 🔑 Environment Variables

### Required
```bash
DASHSCOPE_API_KEY=your_api_key
DASHVECTOR_API_KEY=your_vector_db_key
DASHVECTOR_ENDPOINT=your_endpoint
```

### Background Tasks (Optional)
```bash
ENABLE_BACKGROUND_TASKS=true                    # Default: true
EMBEDDING_MAINTENANCE_INTERVAL_MINUTES=60       # Default: 60
EMBEDDING_MAINTENANCE_BATCH_SIZE=50             # Default: 50
EMBEDDING_MAINTENANCE_MAX_TIME_MINUTES=30       # Default: 30
```

## 📊 Key Metrics to Monitor

### Background Task Health
- Scheduler running: `true`
- Jobs registered: `≥ 2` (STS + embedding)
- Embedding service initialized: `true`

### Embedding Maintenance Stats
- `total_profiles_fixed`: Should increase over time, then stabilize
- `total_projects_fixed`: Should increase over time, then stabilize
- `total_errors`: Should remain low (< 5% of processed)
- `last_run`: Should be recent (within last hour)

### Database Health
```sql
-- Should be 0 after cleanup and maintenance
SELECT COUNT(*) FROM user_profiles WHERE embedding IS NULL AND is_active = true;
SELECT COUNT(*) FROM projects WHERE embedding IS NULL AND is_deleted = false;
```

## 🎯 Performance Tuning

### High Load Server
```bash
EMBEDDING_MAINTENANCE_BATCH_SIZE=100           # Larger batches
EMBEDDING_MAINTENANCE_MAX_TIME_MINUTES=45     # More time per run
EMBEDDING_MAINTENANCE_INTERVAL_MINUTES=30     # More frequent runs
```

### Low Resource Server
```bash
EMBEDDING_MAINTENANCE_BATCH_SIZE=25            # Smaller batches
EMBEDDING_MAINTENANCE_MAX_TIME_MINUTES=15     # Less time per run
EMBEDDING_MAINTENANCE_INTERVAL_MINUTES=120    # Less frequent runs
```

## 🔮 Adding New AI Features

### Quick Start Template
```python
# 1. Add use case to embedding service
# In app/services/embedding_service.py
"your_feature": {
    "model": "text-embedding-v4",
    "dimensions": 1024,
    "description": "For your new feature"
}

# 2. Generate embeddings
from app.services.embedding_service import embedding_service
embedding = embedding_service.generate_embedding(text, use_case="your_feature")

# 3. Implement your feature logic
def your_feature_function(input_text):
    embedding = embedding_service.generate_embedding(input_text, "your_feature")
    # Your AI/ML logic here
    return results
```

## 📞 Support

### Log Locations
- **Application logs**: Check your Flask app log location
- **Background task logs**: Look for patterns like `🔧`, `✅`, `❌` in logs
- **Redis logs**: For caching issues

### Key Log Patterns
```bash
# Successful maintenance
"✅ Embedding maintenance completed: profiles_fixed=X, projects_fixed=Y"

# Errors to investigate
"❌ Error processing profile"
"❌ Failed to generate embedding"
"Vector DB error"

# Performance indicators
"Time limit reached, stopping"
"Found X profiles without embeddings"
```

### Getting Help
1. Check this documentation first
2. Review application logs for specific error messages
3. Verify environment variables are set correctly
4. Test API endpoints manually to isolate issues

Remember: The background task system is designed to be self-healing. Most issues resolve automatically over time as the maintenance tasks run.