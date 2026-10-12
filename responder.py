"""AI auto-responder module using Google Gemini API."""

import logging
from typing import Optional

from google import genai
from google.genai import types

import config
import prompts

logger = logging.getLogger(__name__)


class EmailResponder:
    """Generates context-aware draft replies tailored by tone and category."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        """Initialize the Gemini client.

        Args:
            api_key: Gemini API key. Defaults to config.GEMINI_API_KEY.
            model: Gemini model identifier. Defaults to config.DEFAULT_MODEL.
        """
        self.api_key = api_key or config.GEMINI_API_KEY
        self.model = model or config.DEFAULT_MODEL

        if not self.api_key:
            logger.warning("No Gemini API key provided to EmailResponder.")
            self.client = None
        else:
            self.client = genai.Client(api_key=self.api_key)

    def should_reply(self, category: str) -> bool:
        """Determine whether an email of this category warrants an AI reply.

        Per specifications, no replies are drafted for Spam or Promotions.

        Args:
            category: Classified category string.

        Returns:
            True if reply generation is appropriate, False otherwise.
        """
        if category in ["Spam", "Promotions"]:
            return False
        return True

    def generate_reply(
        self,
        sender: str,
        subject: str,
        body: str,
        category: str,
        tone: str = "Professional",
        custom_instruction: str = "",
    ) -> Optional[str]:
        """Generate a contextual email reply draft.

        Args:
            sender: Original sender's name or email.
            subject: Email subject line.
            body: Body text of the email.
            category: Classified category.
            tone: Tone style ('Professional', 'Friendly', 'Short and direct').
            custom_instruction: Optional guidance from user.

        Returns:
            Draft response text, or None if skipped/error.
        """
        # Rule: Do not generate reply for Spam or Promotions
        if not self.should_reply(category):
            return None

        if not self.client:
            return (
                "Gemini API key is missing. Please configure GEMINI_API_KEY in your .env file "
                "or settings sidebar to enable AI reply generation."
            )

        prompt = prompts.build_reply_prompt(
            sender=sender or "Sender",
            subject=subject or "Inquiry",
            body=body or "",
            category=category or "General",
            tone=tone,
            custom_instruction=custom_instruction,
        )

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=prompts.REPLY_SYSTEM_INSTRUCTION,
                    temperature=0.3,
                ),
            )

            reply_text = response.text.strip() if response.text else ""

            # Check if Gemini flagged as no reply needed
            if "NO_REPLY_NEEDED" in reply_text:
                return None

            return reply_text

        except Exception as exc:
            logger.error("Error generating reply draft: %s", exc)
            return f"Error generating reply draft: {str(exc)}"
