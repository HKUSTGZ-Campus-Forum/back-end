# app/config_security.py - Environment-specific security configurations
import os

class SecurityConfig:
    """Base security configuration"""
    
    @staticmethod
    def get_allowed_origins():
        """Get allowed CORS origins based on environment"""
        env = os.getenv('FLASK_ENV', 'production')
        
        if env == 'development':
            return [
                "http://localhost:3000",
                "http://127.0.0.1:3000",
                "https://dev.unikorn.axfff.com",  # Dev frontend
            ]
        elif env == 'staging':
            return [
                "https://staging.unikorn.axfff.com",
            ]
        else:  # production
            return [
                "https://unikorn.axfff.com",
                "https://www.unikorn.axfff.com",
            ]
    
    @staticmethod
    def get_socketio_origins():
        """Get allowed SocketIO origins (same as CORS)"""
        return SecurityConfig.get_allowed_origins()
    
    @staticmethod
    def is_debug_mode():
        """Check if debug mode should be enabled"""
        env = os.getenv('FLASK_ENV', 'production')
        return env == 'development'
    
    @staticmethod
    def get_log_level():
        """Get appropriate log level"""
        env = os.getenv('FLASK_ENV', 'production')
        if env == 'development':
            return 'DEBUG'
        elif env == 'staging':
            return 'INFO'
        else:
            return 'WARNING'

class DevSecurityConfig(SecurityConfig):
    """Development environment security (more permissive)"""
    
    @staticmethod
    def get_allowed_origins():
        return [
            "http://localhost:3000",
            "http://127.0.0.1:3000", 
            "https://dev.unikorn.axfff.com",
        ]

class ProdSecurityConfig(SecurityConfig):
    """Production environment security (strict)"""
    
    @staticmethod
    def get_allowed_origins():
        return [
            "https://unikorn.axfff.com",
            "https://www.unikorn.axfff.com",
        ]