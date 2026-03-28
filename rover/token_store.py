"""Encrypted storage for per-user Gmail OAuth tokens."""

import json
import os
from datetime import datetime, timezone

from cryptography.fernet import Fernet

from rover.db import Database
from rover.logger import get_logger

logger = get_logger("token_store")


class GmailTokenStore:
    """Encrypts and stores Gmail OAuth tokens in the database, keyed by user_id."""

    def __init__(self, db: Database, encryption_key: str | None = None):
        self.db = db
        key = encryption_key or os.environ.get("GMAIL_TOKEN_ENCRYPTION_KEY")
        if not key:
            raise ValueError(
                "GMAIL_TOKEN_ENCRYPTION_KEY must be provided or set as environment variable. "
                "Generate one with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )
        self.fernet = Fernet(key.encode() if isinstance(key, str) else key)

    def store_token(self, user_id: str, credentials) -> None:
        """Encrypt and store a Google OAuth2 Credentials object.

        Args:
            user_id: The user's ID.
            credentials: A google.oauth2.credentials.Credentials instance.
        """
        token_data = {
            "token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_uri": credentials.token_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "scopes": list(credentials.scopes) if credentials.scopes else [],
        }

        encrypted_access = self.fernet.encrypt(
            (credentials.token or "").encode()
        )
        encrypted_refresh = self.fernet.encrypt(
            json.dumps(token_data).encode()
        )

        token_expiry = None
        if credentials.expiry:
            token_expiry = credentials.expiry.isoformat()

        # Try to get the user's email from the token info
        gmail_email = None
        try:
            if hasattr(credentials, '_id_token') and credentials._id_token:
                gmail_email = credentials._id_token.get("email")
        except Exception:
            pass

        self.db.store_gmail_token(
            user_id=user_id,
            encrypted_access_token=encrypted_access,
            encrypted_refresh_token=encrypted_refresh,
            token_expiry=token_expiry,
            gmail_email=gmail_email,
        )
        logger.info("Stored Gmail token for user %s", user_id)

    def load_credentials(self, user_id: str):
        """Load and decrypt a user's Gmail credentials.

        Args:
            user_id: The user's ID.

        Returns:
            A google.oauth2.credentials.Credentials instance, or None if no token stored.
        """
        from google.oauth2.credentials import Credentials

        row = self.db.get_gmail_token(user_id)
        if not row:
            return None

        try:
            # psycopg2 returns BYTEA as memoryview — convert to bytes
            encrypted = bytes(row["encrypted_refresh_token"])
            decrypted = self.fernet.decrypt(encrypted)
            token_data = json.loads(decrypted)
        except Exception:
            logger.error("Failed to decrypt Gmail token for user %s", user_id)
            return None

        creds = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=token_data.get("client_id"),
            client_secret=token_data.get("client_secret"),
            scopes=token_data.get("scopes"),
        )

        return creds

    def has_token(self, user_id: str) -> bool:
        """Check if a user has a stored Gmail token."""
        return self.db.get_gmail_token(user_id) is not None

    def delete_token(self, user_id: str) -> None:
        """Remove a user's stored Gmail token (disconnect Gmail)."""
        self.db.delete_gmail_token(user_id)
        logger.info("Deleted Gmail token for user %s", user_id)
