# Content Moderation System

This document describes the content moderation system integrated into the campus forum using Aliyun Content Security service.

## Overview

The content moderation system provides real-time content security scanning for all user-generated content (UGC) including posts, comments, gugu messages, and usernames. It uses Aliyun's Content Security API to detect various types of harmful content and maintain community safety.

## Architecture

```
User → Forum API → Content Moderation Service → Aliyun Content Security → Response
```

### Flow Diagram
1. **User submits content** (post/comment/gugu message/username)
2. **Content moderation check** (title + content for posts, content for comments/gugu, username for registration/updates)
3. **Aliyun API call** (real-time content scanning)
4. **Safety evaluation** (risk assessment and violation detection)
5. **Response handling** (allow safe content, block unsafe content)

## Components

### 1. Content Moderation Service (`app/services/content_moderation_service.py`)

Central service that handles all content moderation operations.

**Key Features:**
- Singleton pattern for efficient resource usage
- Flask config integration for consistent credential management
- Comprehensive error handling with fail-safe design
- Detailed logging for monitoring and debugging

**Methods:**
- `moderate_text(content, data_id)` - Moderate any text content
- `moderate_post(title, content, data_id)` - Moderate post title and content
- `moderate_comment(content, data_id)` - Moderate comment content

### 2. API Integration

Content moderation is integrated into the following endpoints:

#### Posts (`/api/posts`)
- **Endpoint:** `POST /api/posts`
- **Integration Point:** After validation, before database insertion
- **Content Moderated:** Post title + content (combined)
- **Response:** 400 error with details if content blocked

#### Comments (`/api/comments`)
- **Endpoint:** `POST /api/comments`
- **Integration Point:** After validation, before database insertion
- **Content Moderated:** Comment content only
- **Response:** 400 error with details if content blocked

#### Gugu Messages (`/api/gugu/messages`)
- **Endpoint:** `POST /api/gugu/messages`
- **Integration Point:** After validation, before database insertion
- **Content Moderated:** Message content only
- **Response:** 400 error with details if content blocked
- **Response Message:** Chinese language ("您的消息违反了社区准则，无法发送")

#### User Registration (`/api/auth/register`)
- **Endpoint:** `POST /api/auth/register`
- **Integration Point:** After basic validation, before user creation
- **Content Moderated:** Username only
- **Response:** 400 error with details if username blocked

#### Username Updates (`/api/users/{id}`)
- **Endpoint:** `PUT /api/users/{id}`
- **Integration Point:** After validation, before username change
- **Content Moderated:** New username only
- **Response:** 400 error with details if username blocked

#### User Creation (`/api/users`)
- **Endpoint:** `POST /api/users`
- **Integration Point:** After validation, before user creation
- **Content Moderated:** Username only
- **Response:** 400 error with details if username blocked

## Configuration

### Environment Variables

Add these variables to your `.env` file:

```bash
# Aliyun Content Security Credentials
ALIBABA_CLOUD_ACCESS_KEY_ID=your_access_key_id
ALIBABA_CLOUD_ACCESS_KEY_SECRET=your_access_key_secret
```

### Service Configuration

The service uses these Aliyun Content Security settings:

```python
region_id='cn-hangzhou'
endpoint='green-cip.cn-hangzhou.aliyuncs.com'
service='comment_detection_pro'
connect_timeout=10000  # 10 seconds
read_timeout=5000      # 5 seconds
```

## Response Format

### Successful Moderation
When content passes moderation, the API proceeds normally with standard responses.

### Blocked Content
When content is blocked, the API returns:

```json
{
  "error": "Content moderation failed",
  "message": "Your content violates community guidelines and cannot be published.",
  "details": "Content violates policy: spam, abuse",
  "risk_level": "high"
}
```

**Response Fields:**
- `error`: Error type identifier
- `message`: User-friendly message
- `details`: Specific reason for blocking
- `risk_level`: Risk assessment (none, low, medium, high)

## Content Policy

The system detects and blocks:

### Violation Categories
- **Spam**: Promotional content, repetitive messages
- **Abuse**: Harassment, bullying, personal attacks
- **Political**: Political content violations
- **Adult**: Adult or inappropriate content
- **Violence**: Violent or harmful content
- **Illegal**: Illegal activities or content

### Risk Levels
- **none**: Content is completely safe
- **low**: Minor concerns, content allowed
- **medium**: Moderate risk, content blocked
- **high**: High risk, content blocked

## Error Handling

### Fail-Safe Design
The system is designed with a fail-safe approach:

- **API Unavailable**: Content is allowed, error logged
- **Network Timeout**: Content is allowed, error logged
- **Invalid Credentials**: Content is allowed, error logged
- **Service Error**: Content is allowed, error logged

This ensures the forum remains functional even if the moderation service experiences issues.

### Logging

All moderation events are logged:

```python
# Successful moderation
logger.info(f"Content moderation result: safe={is_safe}, risk={risk_level}")

# Blocked content
current_app.logger.warning(f"Content moderation blocked post from user {user_id}: {reason}")

# Service errors
logger.error(f"Content moderation error: {error_message}")
```

## Performance Considerations

### API Latency
- **Typical Response Time**: 200-500ms
- **Timeout Settings**: 5s read, 10s connect
- **Impact**: Slight delay in post/comment creation

### Rate Limits
- **Aliyun Limits**: Based on your service plan
- **Recommendation**: Monitor usage and upgrade plan if needed

### Caching
Currently, no caching is implemented for moderation results as content should be checked fresh each time. Future improvements could include:
- Hash-based caching for identical content
- User reputation-based bypass for trusted users

## Monitoring & Maintenance

### Health Checks
Monitor these metrics:
- Content moderation success rate
- API response times
- Error rates and types
- Blocked content statistics

### Log Analysis
Key log patterns to monitor:
```bash
# Successful moderation
grep "Content moderation result" /var/log/app.log

# Blocked content
grep "Content moderation blocked" /var/log/app.log

# Service errors
grep "Content moderation error" /var/log/app.log
```

### Troubleshooting

#### Common Issues

1. **Invalid Credentials**
   ```
   Error: 'NoneType' object has no attribute 'key'
   Solution: Check ALIBABA_CLOUD_ACCESS_KEY_ID and ALIBABA_CLOUD_ACCESS_KEY_SECRET
   ```

2. **Network Timeout**
   ```
   Error: Connection timeout
   Solution: Check network connectivity to cn-hangzhou.aliyuncs.com
   ```

3. **Service Unavailable**
   ```
   Error: API error: 503
   Solution: Check Aliyun service status, verify account balance
   ```

## Development & Testing

### Local Development
For local development without Aliyun credentials:
1. Service will log warnings but allow all content
2. Set up test credentials for full functionality
3. Use the localExps/content_detection.py for API testing

### Testing Content Moderation
```python
from app.services.content_moderation_service import content_moderation

# Test post moderation
result = content_moderation.moderate_post(
    title="Test Title",
    content="Test content",
    data_id="test_123"
)

# Test comment moderation
result = content_moderation.moderate_comment(
    content="Test comment",
    data_id="comment_123"
)
```

## Security Considerations

### Data Privacy
- Content is sent to Aliyun servers for analysis
- No content is stored permanently by the moderation service
- Use data_id for tracking without exposing sensitive information

### Credentials Security
- Store credentials in environment variables only
- Never commit credentials to version control
- Rotate credentials regularly
- Use IAM roles with minimal permissions

## Future Enhancements

### Planned Improvements
1. **Image Moderation**: Extend to moderate uploaded images
2. **Custom Rules**: Add forum-specific moderation rules
3. **User Reputation**: Implement reputation-based moderation
4. **Batch Processing**: Moderate multiple items in one request
5. **Analytics Dashboard**: Real-time moderation statistics
6. **Appeal System**: Allow users to appeal moderation decisions

### Integration Opportunities
- **Notification System**: Alert admins of blocked content
- **User Management**: Automatic user warnings/suspensions
- **Content Analytics**: Trending topics and safety metrics

## Support

For issues related to content moderation:
1. Check logs for specific error messages
2. Verify Aliyun service status
3. Review configuration settings
4. Test with localExps/content_detection.py script

## Version History

- **v1.1** (2025-08-27): Extended coverage
  - Added gugu (instant chat) message moderation
  - Added username moderation for registration and updates
  - Comprehensive UGC protection across all user inputs
  - Localized error messages for gugu in Chinese

- **v1.0** (2025-08-27): Initial implementation
  - Basic text moderation for posts and comments
  - Aliyun Content Security integration
  - Fail-safe error handling
  - Comprehensive logging