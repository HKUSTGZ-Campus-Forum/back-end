"""
Email Service using SMTP for Alibaba Cloud DirectMail
Production-ready email verification and password reset functionality
"""

import smtplib
import ssl
import secrets
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, Any, Optional
from flask import current_app


class EmailService:
    """
    SMTP-based email service for Alibaba Cloud DirectMail
    Handles email verification and password reset emails
    """
    
    def __init__(self, smtp_server: str, smtp_port: int, sender_email: str, 
                 sender_password: str, sender_alias: str):
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.sender_email = sender_email
        self.sender_password = sender_password
        self.sender_alias = sender_alias
    
    @classmethod
    def from_app_config(cls, app=None):
        """Create email service from Flask app configuration"""
        if app is None:
            app = current_app
            
        return cls(
            smtp_server="smtpdm.aliyun.com",
            smtp_port=465,
            sender_email=app.config.get('ALIBABA_DM_ACCOUNT_NAME', 'no-reply@unikorn.axfff.com'),
            sender_password=app.config.get('ALIBABA_CLOUD_EMAIL_SMTP_SECRET'),
            sender_alias=app.config.get('ALIBABA_DM_FROM_ALIAS', 'uniKorn 校园论坛')
        )
    
    def generate_verification_code(self) -> str:
        """Generate 6-digit verification code"""
        return f"{secrets.randbelow(900000) + 100000:06d}"
    
    def generate_reset_token(self) -> str:
        """Generate secure password reset token"""
        return secrets.token_urlsafe(32)
    
    def is_valid_email(self, email: str) -> bool:
        """Basic email validation"""
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return re.match(email_pattern, email) is not None
    
    def _send_email(self, to_email: str, subject: str, html_body: str, 
                   text_body: Optional[str] = None) -> Dict[str, Any]:
        """Send email using SMTP"""
        try:
            # Create email message
            message = MIMEMultipart("alternative")
            message["From"] = f"{self.sender_alias} <{self.sender_email}>"
            message["To"] = to_email
            message["Subject"] = subject
            
            # Add text body if provided
            if text_body:
                text_part = MIMEText(text_body, "plain", "utf-8")
                message.attach(text_part)
            
            # Add HTML body
            html_part = MIMEText(html_body, "html", "utf-8")
            message.attach(html_part)
            
            # Create SSL context and send email
            context = ssl.create_default_context()
            
            with smtplib.SMTP_SSL(self.smtp_server, self.smtp_port, context=context) as server:
                server.login(self.sender_email, self.sender_password)
                server.sendmail(self.sender_email, to_email, message.as_string())
            
            if current_app:
                current_app.logger.info(f"Email sent successfully to {to_email}")
            
            return {
                "success": True,
                "message": "Email sent successfully"
            }
            
        except Exception as e:
            error_msg = f"Failed to send email: {str(e)}"
            if current_app:
                current_app.logger.error(f"Email sending failed to {to_email}: {error_msg}")
            
            return {
                "success": False,
                "error": error_msg
            }
    
    def send_verification_email(self, to_email: str, verification_code: str, 
                              user_name: str = None) -> Dict[str, Any]:
        """Send email verification with code"""
        
        if not self.is_valid_email(to_email):
            return {
                "success": False,
                "error": "Invalid email address format"
            }
        
        subject = "邮箱验证 - uniKorn 校园论坛"
        
        html_template = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>邮箱验证</title>
        </head>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background-color: #f8f9fa; padding: 30px; border-radius: 10px;">
                <h2 style="color: #333; text-align: center;">uniKorn 校园论坛</h2>
                <h3 style="color: #666;">邮箱验证</h3>
                
                <p>{"尊敬的 " + user_name + "，" if user_name else "您好，"}</p>
                
                <p>感谢您注册 uniKorn 校园论坛！请使用以下验证码完成邮箱验证：</p>
                
                <div style="background-color: #007bff; color: white; padding: 15px; text-align: center; border-radius: 5px; margin: 20px 0;">
                    <h2 style="margin: 0; font-size: 32px; letter-spacing: 5px;">{verification_code}</h2>
                </div>
                
                <p>验证码有效期为 10 分钟，请及时使用。</p>
                
                <p>如果您没有注册过我们的服务，请忽略此邮件。</p>
                
                <hr style="border: none; border-top: 1px solid #eee; margin: 30px 0;">
                
                <p style="color: #999; font-size: 12px; text-align: center;">
                    此邮件由系统自动发送，请勿回复。<br>
                    uniKorn 校园论坛 - 连接校园，分享知识
                </p>
            </div>
        </body>
        </html>
        """
        
        text_template = f"""
uniKorn 校园论坛 - 邮箱验证

{"尊敬的 " + user_name + "，" if user_name else "您好，"}

感谢您注册 uniKorn 校园论坛！

您的验证码是：{verification_code}

验证码有效期为 10 分钟，请及时使用。

如果您没有注册过我们的服务，请忽略此邮件。

---
此邮件由系统自动发送，请勿回复。
uniKorn 校园论坛 - 连接校园，分享知识
        """
        
        result = self._send_email(to_email, subject, html_template, text_template)
        
        if current_app:
            if result.get("success"):
                current_app.logger.info(f"Verification email sent to {to_email}")
            else:
                current_app.logger.error(f"Failed to send verification email to {to_email}: {result.get('error')}")
        
        return result
    
    def send_password_reset_email(self, to_email: str, reset_token: str, 
                                user_name: str = None) -> Dict[str, Any]:
        """Send password reset email"""
        
        if not self.is_valid_email(to_email):
            return {
                "success": False,
                "error": "Invalid email address format"
            }
        
        subject = "密码重置 - uniKorn 校园论坛"
        
        # Get frontend URL from configuration
        base_url = current_app.config.get('FRONTEND_BASE_URL', 'https://unikorn.axfff.com') if current_app else 'https://unikorn.axfff.com'
        reset_url = f"{base_url}/reset-password?token={reset_token}"
        
        html_template = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>密码重置</title>
        </head>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background-color: #f8f9fa; padding: 30px; border-radius: 10px;">
                <h2 style="color: #333; text-align: center;">uniKorn 校园论坛</h2>
                <h3 style="color: #666;">密码重置</h3>
                
                <p>{"尊敬的 " + user_name + "，" if user_name else "您好，"}</p>
                
                <p>我们收到了您的密码重置请求。请点击下面的链接重置您的密码：</p>
                
                <div style="text-align: center; margin: 30px 0;">
                    <a href="{reset_url}" 
                       style="background-color: #28a745; color: white; padding: 12px 30px; text-decoration: none; border-radius: 5px; display: inline-block;">
                        重置密码
                    </a>
                </div>
                
                <p>如果按钮无法点击，请复制以下链接到浏览器地址栏：</p>
                <p style="word-break: break-all; color: #007bff;">{reset_url}</p>
                
                <p>此链接有效期为 1 小时。如果您没有请求密码重置，请忽略此邮件。</p>
                
                <hr style="border: none; border-top: 1px solid #eee; margin: 30px 0;">
                
                <p style="color: #999; font-size: 12px; text-align: center;">
                    此邮件由系统自动发送，请勿回复。<br>
                    uniKorn 校园论坛 - 连接校园，分享知识
                </p>
            </div>
        </body>
        </html>
        """
        
        text_template = f"""
uniKorn 校园论坛 - 密码重置

{"尊敬的 " + user_name + "，" if user_name else "您好，"}

我们收到了您的密码重置请求。请复制以下链接到浏览器中重置您的密码：

{reset_url}

此链接有效期为 1 小时。如果您没有请求密码重置，请忽略此邮件。

---
此邮件由系统自动发送，请勿回复。
uniKorn 校园论坛 - 连接校园，分享知识
        """
        
        result = self._send_email(to_email, subject, html_template, text_template)
        
        if current_app:
            if result.get("success"):
                current_app.logger.info(f"Password reset email sent to {to_email}")
            else:
                current_app.logger.error(f"Failed to send password reset email to {to_email}: {result.get('error')}")
        
        return result