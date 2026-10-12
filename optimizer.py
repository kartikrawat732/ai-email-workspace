"""AI Email Proofreader & Optimizer module using Google Gemini API.

Analyzes user drafts, detects classified mistakes across grammar, tone, clarity,
and missing info, and provides an optimized, ready-to-send version.
Strictly preserves the user's actual statements, recipient name, and sender identity.
"""

import html
import json
import logging
import re
from typing import List, Literal, Optional, Tuple
from pydantic import BaseModel, Field

from google import genai
from google.genai import types

import config
import prompts

logger = logging.getLogger(__name__)


class MistakeItem(BaseModel):
    """Specific mistake or weakness detected in a user's draft."""

    category: Literal[
        "Grammar & Spelling",
        "Tone & Politeness",
        "Clarity & Conciseness",
        "Missing Information",
    ] = Field(description="The category of the detected issue.")
    severity: Literal["Critical", "Moderate", "Minor"] = Field(
        description="Impact severity: Critical, Moderate, or Minor."
    )
    original_fragment: str = Field(
        description="The exact phrase or sentence containing the mistake."
    )
    explanation: str = Field(
        description="Why this is considered a mistake and how it will be perceived."
    )
    suggested_fix: str = Field(
        description="Recommended specific correction or alternative wording."
    )


class EmailOptimizationResult(BaseModel):
    """Complete critique, mistake classification, and optimized output."""

    overall_score: int = Field(
        ge=0,
        le=100,
        description="Readiness score from 0 (poor) to 100 (polished and ready to send).",
    )
    tone_detected: str = Field(
        description="Brief summary of the draft's perceived impression or tone.",
    )
    mistakes: List[MistakeItem] = Field(
        default_factory=list,
        description="List of classified mistakes identified across the draft.",
    )
    key_improvements: List[str] = Field(
        default_factory=list,
        description="Bullet points explaining why the optimized version is superior.",
    )
    optimized_subject: str = Field(
        description="High-impact, polished subject line.",
    )
    optimized_body: str = Field(
        description="The rewritten, ready-to-send email body.",
    )


# --- Built-in Sample Draft for Demo & Instant Testing ---
SAMPLE_ROUGH_DRAFT = {
    "recipient": "David (Engineering Director)",
    "audience": "Manager / Executive",
    "tone": "Professional",
    "subject": "quick question about the project deadline delay",
    "body": """hey david,

i wanted to tell you that we cant finish the migration by friday because the database team has not gave us access yet. this always happens and its really frustrating. 

so we are going to be late. also we need more money for cloud servers if you want us to do it faster. let me know what you want to do.

thx
karthik""",
    "objective": "Inform leadership of delay without blaming colleagues and propose actionable paths forward.",
}

# --- Built-in Demo Mock Result for the Sample Draft Only ---
MOCK_OPTIMIZATION_RESULT = EmailOptimizationResult(
    overall_score=52,
    tone_detected="Frustrated, blame-shifting, and overly informal for executive communication",
    mistakes=[
        MistakeItem(
            category="Grammar & Spelling",
            severity="Moderate",
            original_fragment="has not gave us access yet",
            explanation="Grammar error: 'has not given' should be used instead of 'has not gave'.",
            suggested_fix="has not yet granted us access",
        ),
        MistakeItem(
            category="Grammar & Spelling",
            severity="Minor",
            original_fragment="hey david, ... cant ... thx",
            explanation="Uncapitalized greeting, missing apostrophe in 'cant', and informal abbreviation 'thx' undermine credibility.",
            suggested_fix="Hi David, ... cannot ... Best regards,",
        ),
        MistakeItem(
            category="Tone & Politeness",
            severity="Critical",
            original_fragment="this always happens and its really frustrating",
            explanation="Venting frustration and publicly blaming another department in a status update signals lack of emotional maturity and team collaboration.",
            suggested_fix="State the dependency factually without emotional commentary.",
        ),
        MistakeItem(
            category="Clarity & Conciseness",
            severity="Moderate",
            original_fragment="so we are going to be late. also we need more money for cloud servers",
            explanation="Delivers bad news abruptly and makes an unquantified request for 'more money' without justification.",
            suggested_fix="Specify the projected completion date and quantify the infrastructure requirement.",
        ),
        MistakeItem(
            category="Missing Information",
            severity="Critical",
            original_fragment="let me know what you want to do.",
            explanation="Lacks actionable proposals. Effective status communication presents clear options rather than pushing the solution back onto leadership.",
            suggested_fix="Propose two concrete options: a target extension date vs. parallel staging instances.",
        ),
    ],
    key_improvements=[
        "Replaced emotional venting with an objective assessment of the technical dependency.",
        "Elevated casual phrasing ('thx', 'hey') into professional executive correspondence.",
        "Formulated two concrete solution options rather than leaving the delay open-ended.",
        "Included a clear, actionable call-to-action for the director.",
    ],
    optimized_subject="Project Update: Database Migration Timeline & Proposed Next Steps",
    optimized_body="""Hi David,

I am writing to provide a status update on the database migration milestone scheduled for this Friday.

We have encountered a temporary blocker while awaiting access permissions from the database team. Consequently, completing the deployment by Friday without compromising verification testing will not be possible.

To keep the project moving forward effectively, I would like to propose two options:
1. Revised Target (Recommended): Extend the milestone completion to next Tuesday, October 20th, providing adequate time for validation once access is granted.
2. Expedited Parallel Staging: If Friday remains critical, we can provision temporary staging instances to accelerate data ingestion as soon as credentials arrive.

Please let me know which approach works best for you. I would be happy to jump on a quick 5-minute sync if you would like to discuss.

Best regards,
Karthik""",
)


def _dynamic_local_optimize(
    recipient: str,
    subject: str,
    body: str,
    target_tone: str,
    target_audience: str,
    objective: str = "",
) -> EmailOptimizationResult:
    """Dynamically analyze a custom email draft when running offline or without an API key.

    Analyzes the user's actual statements, detects grammatical slips, slang,
    and tone weaknesses, and polishes the user's text while preserving their
    exact recipient, sender names, and core message.
    """
    clean_body = body.strip()
    clean_subj = subject.strip()

    mistakes: List[MistakeItem] = []
    key_improvements: List[str] = []

    # 1. Extract Recipient & Greeting
    recipient_name = ""
    if recipient.strip():
        # Clean recipient e.g. "Sarah Jenkins <sarah@...>" -> "Sarah"
        m_rec = re.match(r"^([a-zA-Z]+)", recipient.strip())
        if m_rec:
            recipient_name = m_rec.group(1)

    greeting_match = re.search(r"^(hi|hello|dear|hey)\s+([a-zA-Z]+)", clean_body, re.IGNORECASE)
    if greeting_match:
        detected_rec = greeting_match.group(2)
        if not recipient_name:
            recipient_name = detected_rec.capitalize()
        greeting_line = f"{greeting_match.group(1).capitalize()} {recipient_name},"
    elif recipient_name:
        greeting_line = f"Hi {recipient_name},"
    else:
        greeting_line = "Hello,"

    # 2. Extract Sign-Off and Sender Name
    sender_name = ""
    lines = [l.strip() for l in clean_body.splitlines() if l.strip()]
    if lines:
        last_line = lines[-1]
        second_last = lines[-2] if len(lines) >= 2 else ""

        # Check if last line is just a name e.g. "Karthik" or "Rahul"
        if re.match(r"^[a-zA-Z]{2,20}$", last_line) and any(
            w in second_last.lower() for w in ["thanks", "regards", "best", "thx", "sincerely", "cheers"]
        ):
            sender_name = last_line.capitalize()
        else:
            # Check for inline sign-off e.g. "Thanks, Rahul" or "thx karthik"
            inline_sign = re.search(
                r"(thanks|thx|best|regards|sincerely|cheers)[\s,]+([a-zA-Z]{2,20})",
                last_line,
                re.IGNORECASE,
            )
            if inline_sign:
                sender_name = inline_sign.group(2).capitalize()

    # 3. Rule-based mistake detection on the user's actual text
    # Check for informal contractions & slang
    slang_map = [
        (r"\bthx\b", "thx", "Informal abbreviation 'thx' diminishes professional presence.", "Thank you / Best regards"),
        (r"\bplz\b", "plz", "Text abbreviation 'plz' is too casual for business correspondence.", "please"),
        (r"\basap\b", "asap", "'ASAP' can be perceived as demanding or impatient.", "at your earliest convenience"),
        (r"\bgonna\b", "gonna", "Colloquial slang 'gonna' should be avoided in written emails.", "going to"),
        (r"\bwanna\b", "wanna", "Colloquial slang 'wanna' is inappropriate for professional communication.", "would like to"),
        (r"\bcant\b", "cant", "Missing apostrophe in contraction 'cant'.", "cannot / can't"),
        (r"\bdont\b", "dont", "Missing apostrophe in contraction 'dont'.", "do not / don't"),
        (r"\bwont\b", "wont", "Missing apostrophe in contraction 'wont'.", "will not / won't"),
        (r"\bim\b", "im", "Missing apostrophe in 'im'.", "I am / I'm"),
        (r"\bive\b", "ive", "Missing apostrophe in 'ive'.", "I have / I've"),
        (r"\bu\b", "u", "Single-letter abbreviation 'u' is unprofessional.", "you"),
        (r"\br\b", "r", "Single-letter abbreviation 'r' is unprofessional.", "are"),
    ]

    for pattern, word, explanation, fix in slang_map:
        if re.search(pattern, clean_body, re.IGNORECASE):
            mistakes.append(
                MistakeItem(
                    category="Grammar & Spelling",
                    severity="Moderate",
                    original_fragment=word,
                    explanation=explanation,
                    suggested_fix=fix,
                )
            )

    # Check for lowercase 'i'
    if re.search(r"\bi\b", clean_body):
        mistakes.append(
            MistakeItem(
                category="Grammar & Spelling",
                severity="Minor",
                original_fragment="i",
                explanation="Uncapitalized standalone pronoun 'i' should always be capitalized.",
                suggested_fix="I",
            )
        )

    # Check for grammar slip: "has not gave"
    if re.search(r"\b(has|have)\s+not\s+gave\b", clean_body, re.IGNORECASE):
        mistakes.append(
            MistakeItem(
                category="Grammar & Spelling",
                severity="Critical",
                original_fragment="has not gave",
                explanation="Grammatical error: 'has not given' or 'has not provided' is correct.",
                suggested_fix="has not yet provided",
            )
        )

    # Check for tone: Overly casual opening with executives/clients
    if re.search(r"^hey\b", clean_body, re.IGNORECASE) and target_audience in ["Manager / Executive", "Client / Customer"]:
        mistakes.append(
            MistakeItem(
                category="Tone & Politeness",
                severity="Moderate",
                original_fragment="hey",
                explanation="Starting with 'hey' when emailing leadership or clients may sound overly informal.",
                suggested_fix="Use 'Hi [Name],' or 'Dear [Name],'",
            )
        )

    # Check for emotional frustration words
    if re.search(r"\b(frustrating|this always happens|annoying|terrible|hate|angry)\b", clean_body, re.IGNORECASE):
        mistakes.append(
            MistakeItem(
                category="Tone & Politeness",
                severity="Critical",
                original_fragment="frustrating / this always happens",
                explanation="Venting frustration or emotional commentary in an email reduces objectivity and can strain professional relationships.",
                suggested_fix="State the technical blocker or challenge objectively without emotional editorializing.",
            )
        )

    # Check for missing call-to-action or abrupt ending
    if "?" not in clean_body and not re.search(r"\b(let me know|please|could you|would you|looking forward)\b", clean_body, re.IGNORECASE):
        mistakes.append(
            MistakeItem(
                category="Missing Information",
                severity="Moderate",
                original_fragment="Ending paragraph",
                explanation="The email lacks a clear question, deadline, or call-to-action indicating what the recipient should do next.",
                suggested_fix="Conclude with a clear request (e.g. 'Please let me know if this works for you.')",
            )
        )

    # Check if subject line is missing or lowercase
    if not clean_subj:
        mistakes.append(
            MistakeItem(
                category="Missing Information",
                severity="Critical",
                original_fragment="Subject Line",
                explanation="Subject line is completely empty. Clear subjects ensure prompt attention.",
                suggested_fix="Add a descriptive subject summarizing the core topic.",
            )
        )
    elif clean_subj.islower():
        mistakes.append(
            MistakeItem(
                category="Clarity & Conciseness",
                severity="Minor",
                original_fragment=clean_subj,
                explanation="All-lowercase subject line looks casual and is easily overlooked.",
                suggested_fix="Capitalize major words using Title Case.",
            )
        )

    # 4. Generate the optimized subject
    if clean_subj:
        # Title case the user's actual subject line
        words = clean_subj.split()
        opt_subj = " ".join([w.capitalize() if w.lower() not in ["a", "an", "the", "in", "on", "at", "for", "to", "about"] or i == 0 else w.lower() for i, w in enumerate(words)])
        # Elevate phrasing if it started with "quick question about"
        opt_subj = re.sub(r"^Quick Question About\s+", "Inquiry: ", opt_subj, flags=re.IGNORECASE)
    else:
        opt_subj = f"Update regarding {objective}" if objective else "Project Update & Next Steps"

    # 5. Generate the optimized body strictly from the user's sentences
    # Extract core body content (excluding greeting and sign-off)
    body_lines = clean_body.splitlines()
    content_lines = []
    for l in body_lines:
        line_clean = l.strip()
        if not line_clean:
            continue
        # Skip initial greeting line
        if re.match(r"^(hi|hello|dear|hey)\b", line_clean, re.IGNORECASE) and len(content_lines) == 0:
            continue
        # Skip sign-off lines
        if re.search(r"^(thanks|thx|best|regards|sincerely|cheers)", line_clean, re.IGNORECASE):
            continue
        if sender_name and line_clean.lower() == sender_name.lower():
            continue
        content_lines.append(line_clean)

    raw_text = " ".join(content_lines) if content_lines else clean_body

    # Clean grammar & slang in the user's text
    polished_text = raw_text
    polished_text = re.sub(r"\bthx\b", "Thank you", polished_text, flags=re.IGNORECASE)
    polished_text = re.sub(r"\bplz\b", "please", polished_text, flags=re.IGNORECASE)
    polished_text = re.sub(r"\basap\b", "at your earliest convenience", polished_text, flags=re.IGNORECASE)
    polished_text = re.sub(r"\bgonna\b", "going to", polished_text, flags=re.IGNORECASE)
    polished_text = re.sub(r"\bwanna\b", "would like to", polished_text, flags=re.IGNORECASE)
    polished_text = re.sub(r"\bcant\b", "cannot", polished_text, flags=re.IGNORECASE)
    polished_text = re.sub(r"\bdont\b", "do not", polished_text, flags=re.IGNORECASE)
    polished_text = re.sub(r"\bwont\b", "will not", polished_text, flags=re.IGNORECASE)
    polished_text = re.sub(r"\bim\b", "I am", polished_text, flags=re.IGNORECASE)
    polished_text = re.sub(r"\bive\b", "I have", polished_text, flags=re.IGNORECASE)
    polished_text = re.sub(r"\bi\b", "I", polished_text)
    polished_text = re.sub(r"\bu\b", "you", polished_text, flags=re.IGNORECASE)
    polished_text = re.sub(r"\br\b", "are", polished_text, flags=re.IGNORECASE)
    polished_text = re.sub(r"\bhas not gave\b", "has not yet provided", polished_text, flags=re.IGNORECASE)

    # Soften emotional venting if found
    polished_text = re.sub(
        r"this always happens and its really frustrating\.?",
        "We are actively coordinating to resolve the dependency as soon as possible.",
        polished_text,
        flags=re.IGNORECASE,
    )

    # Capitalize sentences properly
    sentences = re.split(r"([.!?]+\s*)", polished_text)
    capitalized_sentences = []
    for s in sentences:
        if s and not re.match(r"^[.!?\s]+$", s):
            s_clean = s.strip()
            capitalized_sentences.append(s_clean[:1].upper() + s_clean[1:])
        else:
            capitalized_sentences.append(s)
    polished_content = "".join(capitalized_sentences)

    # Ensure ending punctuation
    if not polished_content.endswith((".", "!", "?")):
        polished_content += "."

    # Build final signature
    sign_off_phrase = "Best regards," if target_tone in ["Professional", "Executive & Authoritative"] else "Thanks,"
    sign_off_block = f"{sign_off_phrase}\n{sender_name}" if sender_name else f"{sign_off_phrase}"

    # Assemble complete rewritten email body
    optimized_body = f"{greeting_line}\n\n{polished_content}\n\n{sign_off_block}"

    # Calculate overall readiness score
    score = 90 - (len(mistakes) * 8)
    score = max(35, min(95, score))

    # Compile key improvements
    key_improvements = [
        "Preserved your exact recipient, statements, and sign-off identity.",
        "Corrected grammatical issues, punctuation, and informal shorthand.",
        f"Elevated language and structure to fit your '{target_tone}' tone.",
    ]
    if mistakes:
        key_improvements.append(f"Addressed {len(mistakes)} detected issue(s) across tone, clarity, and formatting.")

    tone_detected = "Casual / Needs Polish" if mistakes else "Clear and Courteous"

    return EmailOptimizationResult(
        overall_score=score,
        tone_detected=tone_detected,
        mistakes=mistakes,
        key_improvements=key_improvements,
        optimized_subject=opt_subj,
        optimized_body=optimized_body,
    )


class EmailOptimizer:
    """Evaluates draft emails, classifies mistakes, and creates optimized versions."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        """Initialize the Gemini client.

        Args:
            api_key: Gemini API key. Defaults to config.GEMINI_API_KEY.
            model: Gemini model identifier. Defaults to config.DEFAULT_MODEL.
        """
        self.api_key = api_key or config.GEMINI_API_KEY
        self.model = model or config.DEFAULT_MODEL

        if not self.api_key:
            logger.warning("No Gemini API key provided to EmailOptimizer.")
            self.client = None
        else:
            self.client = genai.Client(api_key=self.api_key)

    def optimize_email(
        self,
        recipient: str,
        subject: str,
        body: str,
        target_tone: str = "Professional",
        target_audience: str = "General",
        objective: str = "",
        use_mock_fallback: bool = False,
    ) -> EmailOptimizationResult:
        """Analyze a draft, classify errors, and produce an optimized version.

        Strictly preserves the user's actual statements, recipient, and sender names.

        Args:
            recipient: Target recipient.
            subject: Draft subject.
            body: Draft body text.
            target_tone: Desired tone.
            target_audience: Target audience category.
            objective: User objective.
            use_mock_fallback: Whether to return mock result if no API key is available.

        Returns:
            EmailOptimizationResult with mistakes, improvements, and polished text.
        """
        # 1. Edge case: Empty input
        if not (body or "").strip() and not (subject or "").strip():
            return EmailOptimizationResult(
                overall_score=0,
                tone_detected="Empty email",
                mistakes=[
                    MistakeItem(
                        category="Missing Information",
                        severity="Critical",
                        original_fragment="",
                        explanation="No subject line or email body was provided.",
                        suggested_fix="Type your initial email thoughts before optimizing.",
                    )
                ],
                key_improvements=["Please input an email draft to receive optimization suggestions."],
                optimized_subject="",
                optimized_body="",
            )

        # 2. Check if the user specifically loaded the built-in sample rough draft
        is_exact_sample = (
            body.strip() == SAMPLE_ROUGH_DRAFT["body"].strip()
            and subject.strip() == SAMPLE_ROUGH_DRAFT["subject"].strip()
        )
        if is_exact_sample and not self.client:
            return MOCK_OPTIMIZATION_RESULT

        # 3. If no Gemini client is configured, run smart dynamic local analysis
        if not self.client:
            return _dynamic_local_optimize(
                recipient=recipient,
                subject=subject,
                body=body,
                target_tone=target_tone,
                target_audience=target_audience,
                objective=objective,
            )

        # 4. Live Gemini API execution with strict prompt guardrails
        prompt = prompts.build_optimizer_prompt(
            recipient=recipient,
            subject=subject,
            body=body,
            target_tone=target_tone,
            target_audience=target_audience,
            objective=objective,
        )

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=prompts.OPTIMIZER_SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    response_schema=EmailOptimizationResult,
                    temperature=0.2,
                ),
            )

            # Check parsed Pydantic object
            if hasattr(response, "parsed") and response.parsed:
                if isinstance(response.parsed, EmailOptimizationResult):
                    return response.parsed
                if isinstance(response.parsed, dict):
                    return EmailOptimizationResult(**response.parsed)

            # Fallback raw text parsing
            raw_text = response.text or "{}"
            cleaned_text = raw_text.strip()
            if cleaned_text.startswith("```json"):
                cleaned_text = cleaned_text[7:]
            if cleaned_text.startswith("```"):
                cleaned_text = cleaned_text[3:]
            if cleaned_text.endswith("```"):
                cleaned_text = cleaned_text[:-3]

            data = json.loads(cleaned_text.strip())
            return EmailOptimizationResult(**data)

        except Exception as exc:
            logger.error("Error during live Gemini email optimization: %s", exc)
            # Fall back to dynamic local optimization so the user's actual text is never replaced with David/Karthik!
            return _dynamic_local_optimize(
                recipient=recipient,
                subject=subject,
                body=body,
                target_tone=target_tone,
                target_audience=target_audience,
                objective=objective,
            )
