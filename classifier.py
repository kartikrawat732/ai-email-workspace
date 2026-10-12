"""Email classifier using Google Gemini API with structured outputs."""

import json
import logging
from typing import Literal, Optional
from pydantic import BaseModel, Field

from google import genai
from google.genai import types

import config
import prompts

logger = logging.getLogger(__name__)


class EmailClassification(BaseModel):
    """Structured classification output schema."""

    category: Literal[
        "Work", "Personal", "Spam", "Promotions", "Finance", "Urgent", "Other"
    ] = Field(
        description="The primary category matching the email's intent."
    )
    priority: Literal["High", "Medium", "Low"] = Field(
        description="Priority level: High, Medium, or Low."
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score between 0.0 (uncertain) and 1.0 (highly confident).",
    )
    summary: str = Field(
        description="Concise 1-2 sentence executive summary of the email content.",
    )
    action_needed: bool = Field(
        default=False,
        description="Whether this email requires user action or a reply.",
    )


class EmailClassifier:
    """Classifies emails using Gemini structured outputs."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        """Initialize the Gemini client.

        Args:
            api_key: Gemini API key. Defaults to config.GEMINI_API_KEY.
            model: Model name. Defaults to config.DEFAULT_MODEL.
        """
        self.api_key = api_key or config.GEMINI_API_KEY
        self.model = model or config.DEFAULT_MODEL

        if not self.api_key:
            logger.warning("No Gemini API key provided to EmailClassifier.")
            self.client = None
        else:
            self.client = genai.Client(api_key=self.api_key)

    def classify_email(
        self,
        sender: str,
        subject: str,
        date: str,
        body: str,
    ) -> EmailClassification:
        """Classify a single email into structured metadata.

        Args:
            sender: Email sender address or name.
            subject: Email subject.
            date: Date received.
            body: Cleaned text content of the email.

        Returns:
            EmailClassification instance with category, priority, confidence, and summary.
        """
        # Edge case: Empty or missing email content
        if not (subject or "").strip() and not (body or "").strip():
            return EmailClassification(
                category="Other",
                priority="Low",
                confidence=1.0,
                summary="Empty email with no subject or content.",
                action_needed=False,
            )

        if not self.client:
            return EmailClassification(
                category="Other",
                priority="Medium",
                confidence=0.0,
                summary="Gemini API key missing. Classification skipped.",
                action_needed=False,
            )

        prompt = prompts.build_classification_prompt(
            sender=sender or "Unknown",
            subject=subject or "(No Subject)",
            date=date or "Recent",
            body=body or "(Empty Body)",
        )

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=prompts.CLASSIFICATION_SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    response_schema=EmailClassification,
                    temperature=0.1,
                ),
            )

            # In the Google GenAI SDK, parsed directly returns the Pydantic model
            if hasattr(response, "parsed") and response.parsed:
                if isinstance(response.parsed, EmailClassification):
                    return response.parsed
                # In case parsed is a dict
                if isinstance(response.parsed, dict):
                    return EmailClassification(**response.parsed)

            # Fallback parsing of raw JSON text
            raw_text = response.text or "{}"
            # Clean possible markdown ticks
            cleaned_text = raw_text.strip()
            if cleaned_text.startswith("```json"):
                cleaned_text = cleaned_text[7:]
            if cleaned_text.startswith("```"):
                cleaned_text = cleaned_text[3:]
            if cleaned_text.endswith("```"):
                cleaned_text = cleaned_text[:-3]

            data = json.loads(cleaned_text.strip())
            return EmailClassification(**data)

        except Exception as exc:
            logger.error("Error classifying email: %s", exc)
            return EmailClassification(
                category="Other",
                priority="Medium",
                confidence=0.3,
                summary=f"Classification error: {str(exc)[:120]}",
                action_needed=False,
            )
