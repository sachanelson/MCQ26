"""OAuth token refresh helper for MCQ26 email sending.

Adapted from bubbleSheet/MCQ/token_refresh.py.  Credentials and token are
stored in the MCQ26 directory (not the legacy MCQ directory).
"""
import os
import json
import logging

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = [
    'https://www.googleapis.com/auth/gmail.modify'  # send + read/modify
]


def _get_paths():
    """Return (token_path, credentials_path) relative to this module."""
    mcq26_dir = os.path.dirname(os.path.abspath(__file__))
    token_path = os.path.join(mcq26_dir, 'token.json')
    credentials_path = os.path.join(mcq26_dir, 'credentials.json')
    return token_path, credentials_path


def refresh_token(force_new: bool = False):
    """Refresh the OAuth token or create a new one if needed.

    Args:
        force_new: If True, force a new OAuth flow regardless of current state.

    Returns:
        Credentials instance or None on failure.
    """
    token_path, credentials_path = _get_paths()
    creds = None

    if not force_new and os.path.exists(token_path):
        try:
            with open(token_path, 'r') as token:
                creds = Credentials.from_authorized_user_info(json.load(token), SCOPES)
                logger.info("Loaded existing token from JSON file")
        except Exception as e:
            logger.error(f"Error loading token: {e}")
            creds = None

    if force_new or not creds or not creds.valid:
        try:
            if creds and creds.expired and creds.refresh_token:
                logger.info("Attempting to refresh expired token")
                try:
                    creds.refresh(Request())
                    logger.info("Token refreshed successfully")
                except Exception as e:
                    logger.error(f"Failed to refresh token: {e}")
                    creds = None

            if not creds or not creds.valid:
                if not os.path.exists(credentials_path):
                    logger.error(f"Credentials file not found at {credentials_path}")
                    return None

                logger.info("Creating new token via OAuth flow")
                flow = InstalledAppFlow.from_client_secrets_file(
                    credentials_path, SCOPES)
                creds = flow.run_local_server(port=0)
                logger.info("New token created successfully")

            try:
                with open(token_path, 'w') as token:
                    token.write(creds.to_json())
                    logger.info(f"Token saved to {token_path}")
            except Exception as e:
                logger.error(f"Error saving token: {e}")

        except Exception as e:
            logger.error(f"Error during token refresh/creation: {e}")
            return None

    return creds


def get_gmail_service(force_new_token: bool = False):
    """Get a Gmail API service instance with refreshed credentials.

    Args:
        force_new_token: If True, force a new OAuth flow.

    Returns:
        Gmail API Resource instance or None on failure.
    """
    try:
        creds = refresh_token(force_new=force_new_token)
        if not creds:
            logger.error("Failed to obtain valid credentials")
            return None

        service = build('gmail', 'v1', credentials=creds)
        logger.info("Gmail service created successfully")
        return service
    except Exception as e:
        logger.error(f"Error creating Gmail service: {e}")
        return None
