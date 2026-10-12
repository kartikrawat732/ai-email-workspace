"""Gmail API integration service for OAuth, message fetching, drafting, and sending."""

import base64
import email
from email.mime.text import MIMEText
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from bs4 import BeautifulSoup
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build, Resource
from googleapiclient.errors import HttpError

import config

logger = logging.getLogger(__name__)


class GmailService:
    """Handles OAuth 2.0 authentication and Gmail operations."""

    def __init__(
        self,
        credentials_file: Path = config.CREDENTIALS_FILE,
        token_file: Path = config.TOKEN_FILE,
        scopes: List[str] = config.GMAIL_SCOPES,
    ):
        """Initialize paths and credentials state."""
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.scopes = scopes
        self._service: Optional[Resource] = None

    def is_credentials_file_present(self) -> bool:
        """Check if credentials.json is present on disk."""
        return self.credentials_file.exists()

    def is_authenticated(self) -> bool:
        """Check if a valid or refreshable token exists."""
        if not self.token_file.exists():
            return False

        try:
            creds = Credentials.from_authorized_user_file(
                str(self.token_file), self.scopes
            )
            return creds.valid or (creds.expired and bool(creds.refresh_token))
        except Exception as exc:
            logger.error("Error reading token file: %s", exc)
            return False

    def get_service(self) -> Resource:
        """Retrieve or build the authenticated Google API client service.

        Returns:
            Google API client resource for Gmail v1.

        Raises:
            FileNotFoundError: If credentials.json is missing when first logging in.
            RuntimeError: If authentication fails.
        """
        if self._service:
            return self._service

        creds: Optional[Credentials] = None

        # Load existing cached tokens if present
        if self.token_file.exists():
            try:
                creds = Credentials.from_authorized_user_file(
                    str(self.token_file), self.scopes
                )
            except Exception as exc:
                logger.warning("Corrupted token.json, re-authentication needed: %s", exc)
                creds = None

        # If credentials don't exist or are invalid, refresh or initiate OAuth flow
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    logger.info("Refreshing expired Gmail access token...")
                    creds.refresh(Request())
                except Exception as exc:
                    logger.warning("Token refresh failed (%s). Triggering fresh OAuth.", exc)
                    creds = None

            if not creds:
                if not self.credentials_file.exists():
                    raise FileNotFoundError(
                        f"Missing OAuth credentials file at: '{self.credentials_file}'. "
                        "Please download credentials.json from Google Cloud Console."
                    )

                logger.info("Initiating browser OAuth 2.0 flow...")
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.credentials_file), self.scopes
                )
                creds = flow.run_local_server(port=0)

            # Persist token for subsequent runs
            with open(self.token_file, "w", encoding="utf-8") as token_out:
                token_out.write(creds.to_json())

        self._service = build("gmail", "v1", credentials=creds)
        return self._service

    def disconnect(self) -> None:
        """Remove saved token.json to disconnect the Gmail account."""
        self._service = None
        if self.token_file.exists():
            try:
                self.token_file.unlink()
                logger.info("Removed token.json successfully.")
            except Exception as exc:
                logger.error("Failed to remove token.json: %s", exc)

    def get_user_email(self) -> Optional[str]:
        """Fetch the authenticated user's email address from Gmail profile.

        Returns:
            Email address string or None.
        """
        try:
            service = self.get_service()
            profile = service.users().getProfile(userId="me").execute()
            return profile.get("emailAddress")
        except Exception as exc:
            logger.warning("Could not fetch user profile email: %s", exc)
            return None

    def fetch_unread_emails(self, max_results: int = 10) -> List[Dict[str, Any]]:
        """Fetch the latest unread emails.

        Args:
            max_results: Maximum number of unread emails to retrieve.

        Returns:
            List of parsed email dictionaries.
        """
        service = self.get_service()
        try:
            # Query unread messages in inbox
            response = (
                service.users()
                .messages()
                .list(userId="me", q="is:unread label:INBOX", maxResults=max_results)
                .execute()
            )

            messages_meta = response.get("messages", [])
            emails: List[Dict[str, Any]] = []

            for msg_meta in messages_meta:
                msg_id = msg_meta["id"]
                full_msg = (
                    service.users()
                    .messages()
                    .get(userId="me", id=msg_id, format="full")
                    .execute()
                )
                parsed = self._parse_message(full_msg)
                emails.append(parsed)

            return emails

        except HttpError as error:
            logger.error("Gmail API error while fetching emails: %s", error)
            raise error

    def _parse_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Extract headers and text body from raw Gmail message payload."""
        msg_id = message.get("id", "")
        thread_id = message.get("threadId", "")
        snippet = message.get("snippet", "")
        payload = message.get("payload", {})
        headers = payload.get("headers", [])

        # Extract standard email headers
        header_map = {h["name"].lower(): h["value"] for h in headers if "name" in h and "value" in h}
        subject = header_map.get("subject", "(No Subject)")
        sender = header_map.get("from", "Unknown Sender")
        date_str = header_map.get("date", "Unknown Date")
        message_id_header = header_map.get("message-id", "")

        # Extract text body (prefer text/plain, fallback to cleaned text/html)
        body = self._extract_body(payload)
        if not body.strip():
            body = snippet

        return {
            "id": msg_id,
            "thread_id": thread_id,
            "sender": sender,
            "subject": subject,
            "date": date_str,
            "message_id_header": message_id_header,
            "snippet": snippet,
            "body": body.strip(),
        }

    def _extract_body(self, payload: Dict[str, Any]) -> str:
        """Recursively navigate MIME parts to extract clean text."""
        body_text = ""
        mime_type = payload.get("mimeType", "")

        # Single part plain text
        if mime_type == "text/plain" and "data" in payload.get("body", {}):
            return self._decode_base64(payload["body"]["data"])

        # Single part HTML
        if mime_type == "text/html" and "data" in payload.get("body", {}):
            raw_html = self._decode_base64(payload["body"]["data"])
            return self._clean_html(raw_html)

        # Multipart messages
        parts = payload.get("parts", [])
        html_fallback = ""

        for part in parts:
            part_mime = part.get("mimeType", "")
            if part_mime == "text/plain" and "data" in part.get("body", {}):
                return self._decode_base64(part["body"]["data"])
            elif part_mime == "text/html" and "data" in part.get("body", {}):
                html_fallback = self._clean_html(self._decode_base64(part["body"]["data"]))
            elif "parts" in part:
                # Nested multipart (e.g. multipart/alternative inside multipart/mixed)
                sub_body = self._extract_body(part)
                if sub_body:
                    return sub_body

        return html_fallback or body_text

    @staticmethod
    def _decode_base64(data: str) -> str:
        """Decode URL-safe base64 string handling padding."""
        try:
            padded_data = data + "=" * (-len(data) % 4)
            decoded_bytes = base64.urlsafe_b64decode(padded_data.encode("ASCII"))
            return decoded_bytes.decode("utf-8", errors="replace")
        except Exception as exc:
            logger.warning("Error decoding base64 data: %s", exc)
            return ""

    @staticmethod
    def _clean_html(html_content: str) -> str:
        """Convert HTML email body into clean readable text."""
        try:
            soup = BeautifulSoup(html_content, "html.parser")
            # Remove scripts and style elements
            for tag in soup(["script", "style", "head", "meta", "noscript"]):
                tag.decompose()
            text = soup.get_text(separator="\n")
            # Condense multiple newlines
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("Error stripping HTML: %s", exc)
            return html_content

    def _create_mime_message(
        self,
        to: str,
        subject: str,
        body: str,
        in_reply_to: Optional[str] = None,
    ) -> MIMEText:
        """Construct a MIMEText message with reply threading headers."""
        mime_msg = MIMEText(body, "plain", "utf-8")
        mime_msg["to"] = to

        # Format Subject with 'Re: ' prefix if missing
        if not subject.lower().startswith("re:"):
            mime_msg["subject"] = f"Re: {subject}"
        else:
            mime_msg["subject"] = subject

        if in_reply_to:
            mime_msg["In-Reply-To"] = in_reply_to
            mime_msg["References"] = in_reply_to

        return mime_msg

    def create_draft(
        self,
        thread_id: str,
        to: str,
        subject: str,
        body: str,
        message_id_header: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Save a reply as a draft in Gmail.

        Args:
            thread_id: Gmail thread ID for the draft.
            to: Recipient email address.
            subject: Email subject.
            body: Draft reply text content.
            message_id_header: Original message Message-ID header for threading.

        Returns:
            Gmail draft resource dictionary.
        """
        service = self.get_service()
        mime_msg = self._create_mime_message(to, subject, body, in_reply_to=message_id_header)
        raw_encoded = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode("ASCII")

        draft_body = {
            "message": {
                "raw": raw_encoded,
                "threadId": thread_id,
            }
        }

        return service.users().drafts().create(userId="me", body=draft_body).execute()

    def send_reply(
        self,
        thread_id: str,
        to: str,
        subject: str,
        body: str,
        message_id_header: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a reply directly in Gmail within the active thread.

        Args:
            thread_id: Thread ID to reply into.
            to: Recipient address.
            subject: Subject line.
            body: Body text of the reply.
            message_id_header: Original message Message-ID.

        Returns:
            Sent message resource dictionary.
        """
        service = self.get_service()
        mime_msg = self._create_mime_message(to, subject, body, in_reply_to=message_id_header)
        raw_encoded = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode("ASCII")

        send_body = {
            "raw": raw_encoded,
            "threadId": thread_id,
        }

        return service.users().messages().send(userId="me", body=send_body).execute()

    def mark_as_read(self, message_id: str) -> None:
        """Remove the UNREAD label from a message."""
        service = self.get_service()
        try:
            service.users().messages().modify(
                userId="me",
                id=message_id,
                body={"removeLabelIds": ["UNREAD"]},
            ).execute()
        except Exception as exc:
            logger.warning("Failed to mark message %s as read: %s", message_id, exc)
