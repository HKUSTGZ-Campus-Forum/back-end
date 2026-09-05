# 咕咕 Chat System Deployment Checklist

## 🚀 Backend Deployment Steps

### 1. Database Migration
```bash
cd /Users/zhaoj/Project/campusForum/back-end
python app/scripts/init_gugu_messages.py
```

### 2. Verify New Files Are Ready
Check these new files exist:
- ✅ `/back-end/app/models/gugu_message.py`
- ✅ `/back-end/app/routes/gugu.py`
- ✅ `/back-end/app/scripts/init_gugu_messages.py`
- ✅ Updated `/back-end/app/models/__init__.py`
- ✅ Updated `/back-end/app/routes/__init__.py`

### 3. Test Backend Locally (Optional)
```bash
cd /Users/zhaoj/Project/campusForum/back-end
python run.py
```
Then test: `curl http://localhost:8000/api/gugu/messages`

### 4. Deploy to Dev Server
**Method depends on your deployment setup:**

**Option A: Git-based deployment**
```bash
cd /Users/zhaoj/Project/campusForum/back-end
git add .
git commit -m "Add 咕咕 chat system - messages, API routes, database migration"
git push origin main  # Triggers auto-deployment
```

**Option B: Manual deployment**
- Upload new files to dev server
- Restart the backend service
- Run migration script on server

### 5. Run Migration on Server
```bash
# On the dev server
python app/scripts/init_gugu_messages.py
```

### 6. Verify Deployment
Test endpoints:
```bash
curl https://dev.unikorn.axfff.com/api/gugu/messages
curl https://dev.unikorn.axfff.com/api/gugu/recent
curl https://dev.unikorn.axfff.com/api/gugu/stats
```

## 🔍 Troubleshooting

### If you get 404 errors:
- Backend routes not deployed yet
- Check server logs for import errors
- Verify blueprint registration

### If you get 500 errors:
- Database table not created
- Run migration script
- Check database connection

### If you get CORS errors:
- Verify CORS configuration in `run.py`
- Check origin whitelist includes your frontend URL

## 🧪 Testing Checklist

After deployment:
- [ ] GET `/api/gugu/messages` returns 200
- [ ] GET `/api/gugu/recent` returns 200 
- [ ] GET `/api/gugu/stats` returns 200
- [ ] POST `/api/gugu/messages` returns 401 (without auth)
- [ ] Frontend chat page loads without errors
- [ ] Homepage shows gugu preview section

## 📝 Notes

- The CORS configuration in `run.py` already includes the correct origins
- Database relationships with User model should work automatically
- JWT authentication follows existing patterns
- All endpoints follow existing API response format