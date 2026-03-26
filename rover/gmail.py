import base64
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from rover.logger import get_logger

logger = get_logger("gmail")


class GmailClient:
    """Gmail API client with OAuth2 authentication for fetching emails."""

    def __init__(self, config: dict):
        gmail_config = config.get("gmail", {})
        self.credentials_file = gmail_config.get("credentials_file", "credentials.json")
        self.token_file = gmail_config.get("token_file", "token.json")
        self.scopes = gmail_config.get(
            "scopes", ["https://www.googleapis.com/auth/gmail.readonly"]
        )
        self.search_query = gmail_config.get("search_query", "category:purchases")
        self.service = None

    def authenticate(self) -> None:
        """Authenticate with Gmail API using OAuth2.

        Uses an existing token if valid, refreshes if expired, or runs the
        interactive OAuth2 flow to obtain a new token.
        """
        creds = None
        token_path = Path(self.token_file)

        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), self.scopes)

        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired OAuth2 token")
            creds.refresh(Request())
        elif not creds or not creds.valid:
            if not Path(self.credentials_file).exists():
                raise FileNotFoundError(
                    f"OAuth2 credentials file not found: {self.credentials_file}"
                )
            logger.info("Running OAuth2 authorization flow")
            flow = InstalledAppFlow.from_client_secrets_file(
                self.credentials_file, self.scopes
            )
            creds = flow.run_local_server(port=8080)

        token_path.parent.mkdir(parents=True, exist_ok=True)
        with open(token_path, "w") as f:
            f.write(creds.to_json())
        logger.info("OAuth2 token saved to %s", self.token_file)

        self.service = build("gmail", "v1", credentials=creds)
        logger.info("Gmail service initialized")

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
