# TODO: Full OAuth flow parked — stub only, returns mock in DEBUG mode

"""
Google OAuth integration for SaccoSphere.

This is a stub implementation that returns mock data in DEBUG mode.
Full OAuth flow implementation is parked for future development.
"""

from django.conf import settings


class GoogleOAuthClient:
    """
    Google OAuth client for user authentication.
    
    This is a stub implementation that provides mock responses in DEBUG mode.
    The full OAuth 2.0 flow with Google's APIs is parked for future development.
    """
    
    def exchange_code_for_token(self, auth_code):
        """
        Exchange authorization code for access token.
        
        Args:
            auth_code: Authorization code received from Google OAuth callback
            
        Returns:
            dict: Contains access_token and other OAuth data
        """
        if settings.DEBUG:
            # Mock response for development
            return {
                'access_token': 'mock_access_token_12345',
                'token_type': 'Bearer',
                'expires_in': 3600,
                'refresh_token': 'mock_refresh_token_67890',
                'scope': 'openid email profile'
            }
        
        # TODO: Implement full OAuth flow
        # 1. Exchange code with Google's token endpoint
        # 2. Validate token with Google's userinfo endpoint
        # 3. Handle token refresh and expiration
        raise NotImplementedError("Full OAuth flow not implemented yet")
    
    def get_user_info(self, access_token):
        """
        Get user information from Google using access token.
        
        Args:
            access_token: OAuth access token
            
        Returns:
            dict: User profile information (email, name, picture)
        """
        if settings.DEBUG:
            # Mock user data for development
            return {
                'email': 'user@example.com',
                'given_name': 'John',
                'family_name': 'Doe',
                'picture': 'https://example.com/avatar.jpg'
            }
        
        # TODO: Implement full user info retrieval
        # 1. Call Google's userinfo endpoint with access token
        # 2. Parse and validate response
        # 3. Handle errors and token expiration
        raise NotImplementedError("Full OAuth flow not implemented yet")
