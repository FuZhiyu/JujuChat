"""Twilio request signature validation."""

import logging
from typing import Dict, Any
from urllib.parse import urlencode

from twilio.request_validator import RequestValidator

from .config import Settings

logger = logging.getLogger(__name__)


class TwilioSignatureValidator:
    """Validates Twilio webhook signatures."""
    
    def __init__(self, settings: Settings):
        """Initialize validator with Twilio auth token."""
        self.settings = settings
        self.validator = RequestValidator(settings.twilio_auth_token)
    
    def validate_request(
        self,
        url: str,
        post_vars: Dict[str, Any],
        signature: str
    ) -> bool:
        """
        Validate Twilio webhook request signature.
        
        Args:
            url: The full URL that Twilio called (including https://)
            post_vars: Dictionary of POST parameters
            signature: X-Twilio-Signature header value
            
        Returns:
            True if signature is valid, False otherwise
        """
        try:
            # Convert form data to the format expected by Twilio validator
            # Need to ensure consistent ordering and encoding
            form_data = {}
            for key, value in post_vars.items():
                if isinstance(value, list) and len(value) == 1:
                    form_data[key] = value[0]
                elif isinstance(value, str):
                    form_data[key] = value
                else:
                    # Skip complex values that Twilio wouldn't send
                    continue
            
            is_valid = self.validator.validate(url, form_data, signature)
            
            if not is_valid:
                # Log validation failure (but redact sensitive info)
                logger.warning(
                    "Twilio signature validation failed for URL: %s",
                    url.split('?')[0]  # Remove query params from log
                )
            
            return is_valid
            
        except Exception as e:
            logger.error("Error during signature validation: %s", str(e))
            return False
    
    def build_validation_url(self, base_url: str, secret: str) -> str:
        """
        Build the full URL that should be used for signature validation.
        
        Args:
            base_url: Base URL (e.g., https://rcs.yourdomain.com)
            secret: Secret path component
            
        Returns:
            Full webhook URL for validation
        """
        return f"{base_url.rstrip('/')}/twilio/rcs/{secret}"