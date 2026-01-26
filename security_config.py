"""Security utilities for Skinergy Desktop Uploader"""

import os
import re
import time
from typing import Dict, Tuple


class SecurityConfig:
    """Security config and utilities"""
    
    # API
    API_BASE_URL = os.getenv('API_BASE_URL', 'https://www.skinergy.lol/api')
    UPLOAD_ENDPOINT = os.getenv('UPLOAD_ENDPOINT', 'upload-data')
    
    # Request settings
    SSL_VERIFY = True
    REQUEST_TIMEOUT = int(os.getenv('REQUEST_TIMEOUT', '30'))
    LOG_SENSITIVE_DATA = False
    MAX_REQUESTS_PER_MINUTE = int(os.getenv('MAX_REQUESTS_PER_MINUTE', '10'))
    
    @classmethod
    def get_api_endpoints(cls) -> Dict[str, str]:
        """Get API endpoints"""
        base = cls.API_BASE_URL
        return {
            'base': base,
            'auth_verify': f"{base}/auth/desktop-verify",
            'upload_data': f"{base}/{cls.UPLOAD_ENDPOINT}"
        }
    
    @classmethod
    def sanitize_log_message(cls, message: str) -> str:
        """Strip out tokens, IDs, and other sensitive stuff from logs"""
        if not cls.LOG_SENSITIVE_DATA:
            patterns = [
                # Auth tokens and bearers
                (r'auth[_\s]?token[:\s]*[a-zA-Z0-9\-_]{10,}', 'auth_token: [REDACTED]'),
                (r'Bearer\s+[a-zA-Z0-9\-_]{10,}', 'Bearer [REDACTED]'),
                (r'token[:\s]+[a-zA-Z0-9\-_]{10,}', 'token: [REDACTED]'),
                (r'Received auth token: [a-zA-Z0-9\-_]{10,}', 'Received auth token: [REDACTED]'),
                
                # User and summoner IDs
                (r'user[_\s]?id[:\s]*[a-f0-9\-]{20,}', 'user_id: [REDACTED]'),
                (r'summoner[_\s]?id[:\s]*\d{6,}', 'summoner_id: [REDACTED]'),
                (r'User ID: [a-f0-9\-]{20,}', 'User ID: [REDACTED]'),
                
                # API endpoints and URLs
                (r'API endpoint: https://[^\s]+', 'API endpoint: [REDACTED]'),
                (r'Making request to: https://[^\s]+', 'Making request to: [API_ENDPOINT]'),
                (r'Fetching [a-z]+ from: https://[^\s]+', 'Fetching data from: [LOCAL_ENDPOINT]'),
                
                # Payload details section
                (r'=== PAYLOAD DETAILS ===.*?=== END PAYLOAD DETAILS ===', 
                 '=== PAYLOAD DETAILS ===\n[PAYLOAD CONTENT REDACTED FOR SECURITY]\n=== END PAYLOAD DETAILS ==='),
                
                # Sample data structures
                (r'Sample skin entry:.*?(?=\n\[|\n$)', 'Sample skin entry: [REDACTED]'),
                (r'Sample loot entry:.*?(?=\n\[|\n$)', 'Sample loot entry: [REDACTED]'),
                
                # Response texts that might contain sensitive data
                (r'API response text: .*', 'API response text: [REDACTED]'),
                (r'Verification response text: .*', 'Verification response text: [REDACTED]'),
            ]
            
            sanitized = message
            for pattern, replacement in patterns:
                sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE | re.DOTALL)
            
            return sanitized
        
        return message
    
    @classmethod
    def validate_auth_code(cls, code: str) -> Tuple[bool, str]:
        """Check if code is valid 8-char alphanumeric"""
        if not code or not isinstance(code, str):
            return False, "Authorization code is required"
        
        # Sanitize input - remove any non-alphanumeric characters
        code = re.sub(r'[^A-Z0-9]', '', code.upper())
        
        if len(code) != 8:
            return False, "Authorization code must be exactly 8 characters"
        
        if not re.match(r'^[A-Z0-9]{8}$', code):
            return False, "Authorization code must contain only letters and numbers"
        
        return True, code
    

class RateLimiter:
    """Simple client-side rate limiter"""
    
    def __init__(self, max_requests: int = 10, window_minutes: int = 1):
        self.max_requests = max_requests
        self.window_seconds = window_minutes * 60
        self.requests = []
    
    def can_make_request(self) -> bool:
        """Check if we're still under the rate limit"""
        now = time.time()
        
        # Clean up old requests
        self.requests = [req_time for req_time in self.requests 
                        if now - req_time < self.window_seconds]
        
        # See if we can make another request
        if len(self.requests) < self.max_requests:
            self.requests.append(now)
            return True
        
        return False
    
    def time_until_next_request(self) -> int:
        """How many seconds until we can make another request"""
        if not self.requests:
            return 0
        
        oldest_request = min(self.requests)
        return max(0, int(self.window_seconds - (time.time() - oldest_request)))