import base64
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from rover.logger import get_logger
from rover.token_store import GmailTokenStore

logger = get_logger("gmail")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def get_oauth_config() -> dict:
    """Build OAuth client config from environment variables."""
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ValueError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set")
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def get_auth_url(redirect_uri: str, state: str | None = None) -> str:
    """Generate the Google OAuth authorization URL.

    Args:
        redirect_uri: Where Google redirects after consent (e.g. https://api.tryrover.app/auth/gmail/callback).
        state: Optional opaque state string to pass through the OAuth flow (e.g. user_id).

    Returns:
        The authorization URL to redirect the user to.
    """
    flow = Flow.from_client_config(get_oauth_config(), scopes=SCOPES, redirect_uri=redirect_uri)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return auth_url


def handle_callback(
    code: str,
    redirect_uri: str,
    user_id: str,
    token_store: GmailTokenStore,
) -> str | None:
    """Exchange the OAuth callback for credentials and store them.

    Args:
        code: The authorization code from Google's callback.
        redirect_uri: Must match the redirect_uri used in get_auth_url.
        user_id: The user to store the token for.
        token_store: Encrypted token storage.

    Returns:
        The Gmail email address if available, or None.
    """
    flow = Flow.from_client_config(get_oauth_config(), scopes=SCOPES, redirect_uri=redirect_uri)
    flow.fetch_token(code=code)
    creds = flow.credentials

    token_store.store_token(user_id, creds)

    # Try to get the user's Gmail email
    gmail_email = _get_gmail_email(creds)
    if gmail_email:
        from rover.db import Database
        db = token_store.db
        db.store_gmail_token(
            user_id=user_id,
            encrypted_access_token=db.get_gmail_token(user_id)["encrypted_access_token"],
            encrypted_refresh_token=db.get_gmail_token(user_id)["encrypted_refresh_token"],
            token_expiry=creds.expiry.isoformat() if creds.expiry else None,
            gmail_email=gmail_email,
        )

    logger.info("Gmail connected for user %s (email: %s)", user_id, gmail_email)
    return gmail_email


def _get_gmail_email(creds: Credentials) -> str | None:
    """Fetch the authenticated user's Gmail address."""
    try:
        service = build("gmail", "v1", credentials=creds)
        profile = service.users().getProfile(userId="me").execute()
        return profile.get("emailAddress")
    except Exception:
        logger.debug("Could not fetch Gmail profile email")
        return None


class GmailClient:
    """Gmail API client with per-user OAuth2 authentication."""

    def __init__(self, token_store: GmailTokenStore, search_query: str = "category:purchases"):
        self.token_store = token_store
        self.search_query = search_query
        self.service = None
        self._user_id = None

    def authenticate(self, user_id: str) -> None:
        """Load stored credentials for a user and build the Gmail service.

        Automatically refreshes expired tokens and saves them back.

        Raises:
            ValueError: If no Gmail token is stored for this user.
        """
        creds = self.token_store.load_credentials(user_id)
        if not creds:
            raise ValueError(f"No Gmail token stored for user {user_id}. User must connect Gmail first.")

        if creds.expired and creds.refresh_token:
            logger.info("Refreshing expired OAuth2 token for user %s", user_id)
            creds.refresh(Request())
            self.token_store.store_token(user_id, creds)

        self._user_id = user_id
        self.service = build("gmail", "v1", credentials=creds)
        logger.info("Gmail service initialized for user %s", user_id)

    def send_email(self, to: str, subject: str, html_body: str) -> dict:
        """Send an email via Gmail API.

        Args:
            to: Recipient email address.
            subject: Email subject line.
            html_body: HTML content for the email body.

        Returns:
            Gmail API response dict with message id.
        """
        if self.service is None:
            raise RuntimeError("Gmail client not authenticated. Call authenticate() first.")

        message = MIMEMultipart("alternative")
        message["to"] = to
        message["subject"] = subject
        message.attach(MIMEText(html_body, "html"))

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        result = self.service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()
        logger.info("Email sent to %s: message id %s", to, result.get("id"))
        return result

    def fetch_emails(self, after_date: str | None = None) -> list[dict]:
        """Fetch emails matching the configured search query.

        Args:
            after_date: Optional date filter in YYYY-MM-DD format.
                        Adds "after:YYYY/MM/DD" to the Gmail search query.

        Returns:
            List of message dicts with keys: id, subject, from, date,
            body_text, body_html.
        """
        if self.service is None:
            raise RuntimeError("Gmail client not authenticated. Call authenticate() first.")

        query = self.search_query
        if after_date:
            date_filter = after_date.replace("-", "/")
            query = f"{query} after:{date_filter}"
        logger.info("Searching Gmail with query: %s", query)

        message_ids = self._list_message_ids(query)
        logger.info("Found %d messages matching query", len(message_ids))

        messages = []
        for msg_id in message_ids:
            try:
                msg = self._get_full_message(msg_id)
                if msg:
                    messages.append(msg)
            except Exception:
                logger.exception("Failed to fetch message %s", msg_id)

        return messages

    def _list_message_ids(self, query: str) -> list[str]:
        """Fetch all message IDs matching the query, handling pagination."""
        message_ids = []
        page_token = None

        while True:
            kwargs = {"userId": "me", "q": query, "maxResults": 100}
            if page_token:
                kwargs["pageToken"] = page_token

            response = self.service.users().messages().list(**kwargs).execute()
            batch = response.get("messages", [])
            message_ids.extend(msg["id"] for msg in batch)

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        return message_ids

    def _get_full_message(self, message_id: str) -> dict | None:
        """Fetch a full message by ID and extract relevant fields."""
        raw = (
            self.service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )

        headers = {h["name"].lower(): h["value"] for h in raw["payload"].get("headers", [])}
        body_text, body_html = self._get_message_body(raw["payload"])

        return {
            "id": message_id,
            "subject": headers.get("subject", ""),
            "from": headers.get("from", ""),
            "date": headers.get("date", ""),
            "body_text": body_text,
            "body_html": body_html,
        }

    def _get_message_body(self, payload: dict) -> tuple[str, str]:
        """Extract text/plain and text/html body from a message payload.

        Recursively traverses multipart message structures to find the
        plain text and HTML body parts.

        Returns:
            Tuple of (plain_text_body, html_body). Either may be empty.
        """
        text_body = ""
        html_body = ""

        mime_type = payload.get("mimeType", "")
        body_data = payload.get("body", {}).get("data")

        if body_data and mime_type == "text/plain":
            text_body = self._decode_body(body_data)
        elif body_data and mime_type == "text/html":
            html_body = self._decode_body(body_data)

        for part in payload.get("parts", []):
            part_text, part_html = self._get_message_body(part)
            if part_text and not text_body:
                text_body = part_text
            if part_html and not html_body:
                html_body = part_html

        return text_body, html_body

    @staticmethod
    def _decode_body(data: str) -> str:
        """Decode a base64url-encoded email body part."""
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
