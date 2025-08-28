# Deployment Notes for Identity Verification System

## Current Status
- ✅ Identity verification system implemented in `identity-verification-system` branch
- ✅ Manual migration created: `create_identity_tables.py`
- ✅ Updated `init_db.py` to handle identity types initialization
- ✅ GitHub Actions workflow supports the deployment

## Safe Deployment Process

### Option 1: Use Existing GitHub Workflow (Recommended)
The current workflow should handle the identity system deployment automatically:

1. **Merge to main**: 
   ```bash
   git checkout main
   git merge identity-verification-system
   git push origin main
   ```

2. **Monitor deployment**: Watch GitHub Actions and server logs

3. **Verify after deployment**:
   ```bash
   # Check API works
   curl https://dev.unikorn.axfff.com/api/identities/types
   
   # Should return 4 identity types
   ```

### Option 2: Manual Deployment (If Issues)
If GitHub Actions fails:

1. **SSH to server**:
   ```bash
   ssh your-server
   cd /data/dev_unikorn/back-end/
   ```

2. **Pull and deploy manually**:
   ```bash
   git pull origin main
   source venv/bin/activate
   pip install -r requirements.txt
   flask db upgrade  # Skip 'flask db migrate' to avoid conflicts
   python -m app.scripts.init_db
   sudo systemctl restart dev-unikorn-api.service
   ```

## Migration Conflict Prevention
The migration file includes `ON CONFLICT` handling to prevent duplicate identity types if both the migration and init_db script try to create them.

## Post-Deployment Checklist
- [ ] Identity types API returns 4 types
- [ ] No 500 errors in logs
- [ ] Existing posts/comments still work
- [ ] Can create new verification requests
- [ ] Admin users can see pending verifications page

## Rollback Plan
If deployment fails:
```bash
git checkout main~1  # Go to previous commit
# Or restore from backup created by workflow
```

## Admin Setup
After successful deployment, make yourself an admin:
```python
from app import create_app
from app.models.user import User
from app.models.user_role import UserRole
from app.extensions import db

app = create_app()
with app.app_context():
    admin_role = UserRole.query.filter_by(name='admin').first()
    your_user = User.query.filter_by(username='your_username').first()
    if admin_role and your_user:
        your_user.role_id = admin_role.id
        db.session.commit()
        print(f"{your_user.username} is now an admin")
```