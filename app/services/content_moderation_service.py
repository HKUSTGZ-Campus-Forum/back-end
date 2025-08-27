# coding=utf-8
"""
Content Moderation Service using Aliyun Content Security
Provides text content moderation for posts and comments
"""

from alibabacloud_green20220302.client import Client
from alibabacloud_green20220302 import models
from alibabacloud_tea_openapi.models import Config
import json
import logging
from flask import current_app
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class ContentModerationService:
    """Aliyun Content Security Service for UGC moderation"""
    
    def __init__(self):
        self.client = None
    
    def _get_client(self):
        """Get or create Aliyun Green client using Flask config"""
        if not self.client:
            try:
                config = Config(
                    access_key_id=current_app.config['ALIBABA_CLOUD_ACCESS_KEY_ID'],
                    access_key_secret=current_app.config['ALIBABA_CLOUD_ACCESS_KEY_SECRET'],
                    connect_timeout=10000,  # 10 seconds
                    read_timeout=5000,      # 5 seconds
                    region_id='cn-hangzhou',
                    endpoint='green-cip.cn-hangzhou.aliyuncs.com'
                )
                self.client = Client(config)
                logger.info("Content moderation client initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize content moderation client: {e}")
                self.client = None
        return self.client
    
    def moderate_text(self, content: str, data_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Moderate text content using Aliyun Content Security
        
        Args:
            content (str): Text content to moderate
            data_id (str): Optional unique identifier for the content
            
        Returns:
            dict: Moderation result with keys:
                - is_safe (bool): Whether content is safe
                - risk_level (str): Risk level (none, low, medium, high)
                - labels (list): Detected violation labels
                - advice (list): Moderation advice
                - reason (str): Rejection reason if unsafe
        """
        client = self._get_client()
        
        if not client:
            logger.warning("Content moderation client not available, allowing content")
            return {
                'is_safe': True,
                'risk_level': 'none',
                'labels': [],
                'advice': [],
                'reason': 'Moderation service unavailable'
            }
        
        if not content or not content.strip():
            return {
                'is_safe': True,
                'risk_level': 'none', 
                'labels': [],
                'advice': [],
                'reason': 'Empty content'
            }
        
        try:
            # Prepare request parameters
            service_parameters = {
                'content': content.strip()
            }
            if data_id:
                service_parameters['dataId'] = data_id
            
            request = models.TextModerationPlusRequest(
                service='comment_detection_pro',
                service_parameters=json.dumps(service_parameters)
            )
            
            # Make API call
            response = client.text_moderation_plus(request)
            
            if response.status_code == 200:
                # Parse response using direct attribute access
                result_data = response.body.data
                
                # Extract risk level
                risk_level = getattr(result_data, 'risk_level', 'none')
                labels = []
                advice = getattr(result_data, 'advice', [])
                
                # Extract result items
                result_items = getattr(result_data, 'result', [])
                if result_items:
                    for item in result_items:
                        label = getattr(item, 'label', 'nonLabel')
                        description = getattr(item, 'description', '')
                        confidence = getattr(item, 'confidence', 0)
                        
                        if label and label != 'nonLabel':
                            labels.append({
                                'label': label,
                                'description': description,
                                'confidence': confidence
                            })
                
                # Determine if content is safe
                is_safe = risk_level in ['none', 'low'] and len(labels) == 0
                
                # Generate reason for unsafe content
                reason = ''
                if not is_safe:
                    if labels:
                        label_names = [label['label'] for label in labels]
                        reason = f"Content violates policy: {', '.join(label_names)}"
                    else:
                        reason = f"Content risk level: {risk_level}"
                
                logger.info(f"Content moderation result: safe={is_safe}, risk={risk_level}")
                
                return {
                    'is_safe': is_safe,
                    'risk_level': risk_level,
                    'labels': labels,
                    'advice': advice,
                    'reason': reason,
                    'raw_response': result
                }
            else:
                logger.error(f"Content moderation API error: {response.status_code}")
                # Fail safe - allow content if API fails
                return {
                    'is_safe': True,
                    'risk_level': 'none',
                    'labels': [],
                    'advice': [],
                    'reason': f'API error: {response.status_code}'
                }
                
        except Exception as e:
            logger.error(f"Content moderation error: {e}")
            # Fail safe - allow content if service fails
            return {
                'is_safe': True,
                'risk_level': 'none',
                'labels': [],
                'advice': [],
                'reason': f'Service error: {str(e)}'
            }
    
    def moderate_post(self, title: str, content: str, data_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Moderate post title and content
        
        Args:
            title (str): Post title
            content (str): Post content
            data_id (str): Optional unique identifier
            
        Returns:
            dict: Combined moderation result
        """
        # Combine title and content for moderation
        combined_content = f"{title}\n\n{content}"
        return self.moderate_text(combined_content, data_id)
    
    def moderate_comment(self, content: str, data_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Moderate comment content
        
        Args:
            content (str): Comment content
            data_id (str): Optional unique identifier
            
        Returns:
            dict: Moderation result
        """
        return self.moderate_text(content, data_id)

# Global instance
content_moderation = ContentModerationService()