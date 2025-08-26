# Redis Caching Deployment Checklist

## Pre-Deployment Checklist

### ✅ Server Requirements
- [ ] Redis server 6.0+ installed and running
- [ ] Python 3.9+ with pip available
- [ ] PostgreSQL database accessible
- [ ] OSS credentials and bucket configured
- [ ] SSL certificates ready (for HTTPS)

### ✅ Environment Configuration
- [ ] **Production**: `REDIS_URL=redis://localhost:6379/0` in `.env`
- [ ] **Development**: `REDIS_URL=redis://localhost:6379/1` in `.env`
- [ ] All other environment variables set
- [ ] Redis memory limits configured (`maxmemory 512mb`)
- [ ] Redis persistence enabled (`save 900 1`)

### ✅ Code Deployment
- [ ] Latest codebase pulled/deployed
- [ ] Dependencies installed (`pip install -r requirements.txt`)
- [ ] Database migrations applied (`flask db upgrade`)
- [ ] Redis caching code merged (File model, cache service, routes)

## Deployment Steps

### 1. Install Redis Dependencies
```bash
# On your server
cd back-end
pip install redis flask-caching
```

### 2. Configure Redis
```bash
# Edit Redis configuration
sudo nano /etc/redis/redis.conf

# Add these lines:
maxmemory 512mb
maxmemory-policy allkeys-lru
save 900 1
bind 127.0.0.1
```

### 3. Restart Services
```bash
# Restart Redis
sudo systemctl restart redis

# Restart your Flask application
sudo systemctl restart your-flask-app
# or if using PM2:
pm2 restart your-app
```

### 4. Verify Installation
```bash
# Test Redis connection
redis-cli ping  # Should return: PONG

# Test cache with Python
python test_redis_cache.py

# Check Flask app logs for cache messages
tail -f /var/log/your-app/app.log | grep "cache"
```

## Post-Deployment Verification

### ✅ Immediate Tests (0-5 minutes)
- [ ] Redis service running (`systemctl status redis`)
- [ ] Flask app starts without errors
- [ ] Basic cache operations work (`redis-cli set test 1; redis-cli get test`)
- [ ] Application responds to health checks

### ✅ Functional Tests (5-15 minutes)
- [ ] File URLs generate and cache properly
- [ ] Cache hits/misses appear in logs
- [ ] Different environments use different Redis databases
- [ ] Admin cache endpoints respond (if admin user available)

### ✅ Performance Tests (15-30 minutes)
- [ ] Cache hit ratio >50% after some usage
- [ ] Image loading feels faster
- [ ] OSS API call frequency reduced
- [ ] Redis memory usage reasonable

## Monitoring Setup

### ✅ Log Monitoring
```bash
# Watch for cache events
tail -f /var/log/your-app/app.log | grep "cached\|cache"

# Watch Redis logs
tail -f /var/log/redis/redis-server.log
```

### ✅ Performance Monitoring
```bash
# Monitor Redis in real-time
redis-cli MONITOR

# Check memory usage
redis-cli INFO memory

# Check cache statistics
redis-cli INFO stats
```

### ✅ Automated Monitoring
- [ ] Add Redis health check to monitoring system
- [ ] Set up alerts for Redis memory usage >80%
- [ ] Monitor cache hit ratio <70%
- [ ] Track OSS API call reduction

## Rollback Plan

If issues occur, here's the rollback procedure:

### 1. Emergency Rollback (keeps Redis, disables caching)
```bash
# Temporarily disable cache by setting fallback in File.url
# Edit app/models/file.py and comment out cache lines
# Restart Flask app
sudo systemctl restart your-flask-app
```

### 2. Full Rollback (removes Redis dependency)
```bash
# Revert to previous codebase version
git checkout previous-working-commit

# Restart without Redis
sudo systemctl restart your-flask-app

# Redis can keep running (won't interfere)
```

## Performance Expectations

### Week 1 (Cache Warm-up Period)
- **Cache Hit Ratio**: 40-60%
- **OSS Call Reduction**: 40-60%
- **Load Time Improvement**: 20-40%

### Week 2+ (Steady State)
- **Cache Hit Ratio**: 80-95%
- **OSS Call Reduction**: 90%+
- **Load Time Improvement**: 50-80%

### Red Flags (Investigate Immediately)
- Cache hit ratio <30% after 24 hours
- Redis memory usage >90%
- Application errors mentioning cache
- No reduction in OSS traffic

## Maintenance Schedule

### Daily
- [ ] Check Redis memory usage
- [ ] Monitor error logs for cache issues
- [ ] Verify both environments working

### Weekly  
- [ ] Review cache hit ratios
- [ ] Check OSS usage reduction
- [ ] Clean up expired cache entries (automatic)

### Monthly
- [ ] Review Redis performance metrics
- [ ] Update cache timeout if needed
- [ ] Test cache warm-up procedures

## Common Issues & Solutions

### Issue: Cache not working
**Symptoms**: No cache logs, same performance as before
**Solution**:
```bash
# Check Redis connection
redis-cli ping

# Check Flask config
python -c "from app import create_app; app=create_app(); print(app.config.get('CACHE_TYPE'))"

# Restart Flask app
sudo systemctl restart your-flask-app
```

### Issue: Wrong database being used
**Symptoms**: Dev/prod sharing cache entries
**Solution**:
```bash
# Check .env files have correct REDIS_URL
# Production: redis://localhost:6379/0
# Development: redis://localhost:6379/1

# Clear wrong database
redis-cli -n 0 FLUSHDB  # Clear production
redis-cli -n 1 FLUSHDB  # Clear development
```

### Issue: High memory usage
**Symptoms**: Redis using >512MB RAM
**Solution**:
```bash
# Set memory limit
redis-cli CONFIG SET maxmemory 256mb
redis-cli CONFIG SET maxmemory-policy allkeys-lru

# Check large keys
redis-cli --bigkeys
```

## Success Metrics

**Technical Metrics:**
- Redis running with <512MB memory usage
- Cache hit ratio >80% after warm-up
- Application startup time <30 seconds
- Zero cache-related errors in logs

**Business Metrics:**
- OSS bandwidth costs reduced by >70%
- User-reported faster image loading
- Reduced server load during peak hours
- Improved user engagement metrics

**Final Verification:**
- [ ] Both environments working independently
- [ ] Cache statistics accessible via admin API
- [ ] Monitoring alerts configured and tested
- [ ] Documentation updated and team notified

---

🎉 **Deployment Complete!** 

Your Redis caching system should now be dramatically reducing OSS traffic and improving user experience. Monitor the metrics above and enjoy the performance improvements!

For detailed troubleshooting, see [REDIS_CACHING.md](./REDIS_CACHING.md).