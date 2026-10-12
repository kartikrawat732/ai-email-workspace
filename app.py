"""AI Email Workspace - Streamlit Web Application.

Features:
1. Inbox Triage & Auto-Responder: Categorizes incoming emails and drafts contextual replies.
   Uses MySQL persistence to skip duplicate Gemini API calls.
2. AI Email Proofreader & Optimizer: Identifies mistakes in draft emails and provides optimal revisions.
3. History & Analytics: Cloud MySQL persistence with Aiven (SSL), timeline charts, and user data deletion.
"""

import html
import logging
from typing import Dict, List, Optional
import pandas as pd
import streamlit as st

import config
from classifier import EmailClassifier, EmailClassification
from database import DatabaseManager
from gmail_service import GmailService
from responder import EmailResponder
from optimizer import (
    EmailOptimizer,
    EmailOptimizationResult,
    MistakeItem,
    SAMPLE_ROUGH_DRAFT,
    MOCK_OPTIMIZATION_RESULT,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Streamlit Page Configuration ---
st.set_page_config(
    page_title="AI Email Workspace",
    page_icon="📬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- Custom Styling ---
st.markdown(
    """
    <style>
    .metric-card {
        background: rgba(255, 255, 255, 0.05);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 12px;
        padding: 18px;
        text-align: center;
        margin-bottom: 12px;
    }
    .badge {
        display: inline-block;
        padding: 3px 10px;
        font-size: 0.8rem;
        font-weight: 600;
        border-radius: 9999px;
        color: #ffffff;
        margin-right: 6px;
    }
    .email-container {
        border: 1px solid rgba(128, 128, 128, 0.2);
        border-radius: 10px;
        padding: 16px;
        margin-bottom: 16px;
        background-color: rgba(255, 255, 255, 0.02);
    }
    .email-body-box {
        background-color: rgba(0, 0, 0, 0.15);
        border: 1px solid rgba(128, 128, 128, 0.2);
        border-radius: 8px;
        padding: 14px;
        font-family: monospace;
        font-size: 0.88rem;
        white-space: pre-wrap;
        max-height: 280px;
        overflow-y: auto;
    }
    .summary-box {
        background: rgba(37, 99, 235, 0.08);
        border-left: 4px solid #2563EB;
        padding: 10px 14px;
        border-radius: 4px;
        margin: 8px 0;
        font-size: 0.92rem;
    }
    .mistake-card {
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid rgba(128, 128, 128, 0.25);
        border-radius: 10px;
        padding: 14px;
        margin-bottom: 12px;
    }
    .mistake-quote {
        background: rgba(239, 68, 68, 0.1);
        border-left: 3px solid #EF4444;
        padding: 6px 12px;
        font-family: monospace;
        font-size: 0.86rem;
        border-radius: 2px;
        margin: 6px 0;
    }
    .suggestion-box {
        background: rgba(16, 185, 129, 0.1);
        border-left: 3px solid #10B981;
        padding: 6px 12px;
        font-size: 0.88rem;
        border-radius: 2px;
        margin: 6px 0;
    }
    .privacy-notice {
        background: rgba(16, 185, 129, 0.08);
        border-left: 4px solid #10B981;
        padding: 10px 16px;
        border-radius: 4px;
        font-size: 0.88rem;
        margin-bottom: 16px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def render_badge(text: str, bg_color: str) -> str:
    """Return an HTML span badge."""
    escaped_text = html.escape(text)
    return f'<span class="badge" style="background-color: {bg_color};">{escaped_text}</span>'


# --- Cached Database Connection Manager ---
@st.cache_resource
def get_database_manager() -> DatabaseManager:
    """Initialize singleton DatabaseManager with automatic schema execution."""
    db = DatabaseManager()
    if config.is_database_configured():
        is_ok, _ = db.check_connection()
        if is_ok:
            db.init_db()
    return db


db_manager = get_database_manager()


# --- Session State Initialization ---
def init_session_state() -> None:
    """Initialize persistent session state variables."""
    # Inbox Triage state
    if "emails" not in st.session_state:
        st.session_state.emails = []
    if "classifications" not in st.session_state:
        st.session_state.classifications = {}
    if "replies" not in st.session_state:
        st.session_state.replies = {}
    if "action_status" not in st.session_state:
        st.session_state.action_status = {}
    if "db_email_ids" not in st.session_state:
        st.session_state.db_email_ids = {}

    # Email Optimizer state
    if "opt_recipient" not in st.session_state:
        st.session_state.opt_recipient = ""
    if "opt_subject" not in st.session_state:
        st.session_state.opt_subject = ""
    if "opt_body" not in st.session_state:
        st.session_state.opt_body = ""
    if "opt_audience" not in st.session_state:
        st.session_state.opt_audience = "Manager / Executive"
    if "opt_tone" not in st.session_state:
        st.session_state.opt_tone = "Professional"
    if "opt_objective" not in st.session_state:
        st.session_state.opt_objective = ""
    if "optimization_result" not in st.session_state:
        st.session_state.optimization_result = None
    if "opt_action_status" not in st.session_state:
        st.session_state.opt_action_status = None


init_session_state()

# --- Sample Mock Data for Instant Testing ---
MOCK_EMAILS = [
    {
        "id": "mock-001",
        "thread_id": "thread-001",
        "sender": "Sarah Jenkins <sarah.jenkins@company.com>",
        "subject": "Urgent: Production API latency spike affecting checkout",
        "date": "Today, 10:15 AM",
        "message_id_header": "<mock-001@company.com>",
        "snippet": "We are observing a 400ms latency spike in the checkout service. Can you jump on the triage call immediately?",
        "body": "Hi team,\n\nWe are observing a 400ms latency spike in the checkout service since 09:45 UTC. Customers are experiencing timeout errors at payment processing.\n\nCould you please jump on the emergency bridge and help inspect the database query logs?\n\nBridge Link: https://meet.google.com/emergency-bridge\n\nBest,\nSarah Jenkins\nLead DevOps Engineer",
    },
    {
        "id": "mock-002",
        "thread_id": "thread-002",
        "sender": "Alex Miller <alex.m@clientcorp.io>",
        "subject": "Q3 Contract Renewal and Scope Discussion",
        "date": "Yesterday, 4:30 PM",
        "message_id_header": "<mock-002@clientcorp.io>",
        "snippet": "Following up on our discussion last week regarding the Q3 contract renewal and feature additions.",
        "body": "Hi,\n\nHope you are having a productive week.\n\nFollowing up on our call last week regarding the Q3 contract renewal. We would like to expand the scope to include 5 additional team seats and dedicated priority support.\n\nCould you provide an updated price estimate by Friday? Also let me know if Tuesday 2 PM works for a quick 15-minute sync.\n\nThanks,\nAlex Miller\nVP of Product, ClientCorp",
    },
    {
        "id": "mock-003",
        "thread_id": "thread-003",
        "sender": "CloudServices Billing <billing@cloudservices.net>",
        "subject": "Invoice #INV-2026-8942 Available for Download",
        "date": "Oct 10, 2026, 8:00 AM",
        "message_id_header": "<mock-003@cloudservices.net>",
        "snippet": "Your monthly cloud hosting invoice for October is ready. Amount due: $249.00.",
        "body": "Dear Customer,\n\nYour monthly statement for billing cycle Sep 1 - Sep 30, 2026 is now available.\n\nInvoice Number: INV-2026-8942\nTotal Amount: $249.00 USD\nDue Date: October 24, 2026\nPayment Method: Auto-charge to Visa ending in 4112\n\nYou can view and download the PDF tax receipt from your account dashboard.\n\nThank you for choosing CloudServices.",
    },
    {
        "id": "mock-004",
        "thread_id": "thread-004",
        "sender": "SaaSToolbox Deals <newsletter@saastoolbox.com>",
        "subject": "🚀 Flash Sale: 50% off all Pro subscriptions this weekend only!",
        "date": "Oct 09, 2026, 11:15 AM",
        "message_id_header": "<mock-004@saastoolbox.com>",
        "snippet": "Unlock enterprise AI features for half the price! Limited time offer ends Sunday midnight.",
        "body": "Hey there,\n\nSupercharge your workflow with SaaSToolbox Pro! For the next 48 hours, enjoy an unprecedented 50% discount on all annual tiers.\n\nUse coupon code FLASH50 at checkout.\n\nClick here to claim your discount: https://saastoolbox.com/deal\n\nTo manage your preferences or unsubscribe, click here.",
    },
    {
        "id": "mock-005",
        "thread_id": "thread-005",
        "sender": "Mom <mom@familymail.org>",
        "subject": "Dinner this Sunday?",
        "date": "Oct 08, 2026, 6:45 PM",
        "message_id_header": "<mock-005@familymail.org>",
        "snippet": "Are you free to come over for dinner this Sunday around 6 PM? Making your favorite lasagna.",
        "body": "Hi honey,\n\nHope you're having a good week! Are you free to come over for Sunday dinner around 6:00 PM? Dad and I are making your favorite lasagna.\n\nLet me know if that time works or if another day is better.\n\nLove,\nMom",
    },
]

MOCK_FALLBACK_CLASSIFICATIONS = {
    "mock-001": EmailClassification(
        category="Urgent",
        priority="High",
        confidence=0.98,
        summary="Critical production checkout latency spike (400ms); immediate attendance on emergency bridge requested.",
        action_needed=True,
    ),
    "mock-002": EmailClassification(
        category="Work",
        priority="Medium",
        confidence=0.94,
        summary="Client requesting updated price quote for 5 additional seats and scheduling a 15-min sync for Tuesday.",
        action_needed=True,
    ),
    "mock-003": EmailClassification(
        category="Finance",
        priority="Low",
        confidence=0.99,
        summary="Monthly cloud hosting invoice for $249.00 auto-charged to Visa; receipt ready for download.",
        action_needed=False,
    ),
    "mock-004": EmailClassification(
        category="Promotions",
        priority="Low",
        confidence=0.97,
        summary="48-hour promotional flash sale offer offering 50% discount on SaaSToolbox Pro subscription.",
        action_needed=False,
    ),
    "mock-005": EmailClassification(
        category="Personal",
        priority="Medium",
        confidence=0.95,
        summary="Mom asking if recipient is available for family lasagna dinner this Sunday at 6:00 PM.",
        action_needed=True,
    ),
}

MOCK_FALLBACK_REPLIES = {
    "mock-001": "Hi Sarah,\n\nJoining the emergency bridge immediately to inspect the database query logs and checkout metrics.\n\nBest,\n",
    "mock-002": "Hi Alex,\n\nThanks for reaching out! I'm preparing the updated quote with 5 additional seats and priority support and will have it over to you before Friday. Tuesday at 2 PM works great for a quick sync—I'll send a calendar invite.\n\nBest,\n",
    "mock-005": "Hi Mom,\n\nI'd love to! Sunday at 6:00 PM works perfectly. Looking forward to the lasagna and catching up with you and Dad.\n\nLove,\n",
}

# --- Sidebar Configuration & OAuth Controls ---
st.sidebar.title("⚙️ Settings & Controls")

demo_mode = st.sidebar.toggle(
    "🧪 Demo / Mock Mode",
    value=True,
    help="Test the full dashboard and AI workflow immediately with realistic sample data without needing Google Cloud credentials.",
)

# Database Status Indicator in Sidebar
is_db_connected, db_status_msg = db_manager.check_connection()
if is_db_connected:
    st.sidebar.success("🟢 Aiven MySQL: Connected (SSL)")
else:
    st.sidebar.caption("⚪ MySQL: Offline / Local Mode")

# API Key input
api_key_input = st.sidebar.text_input(
    "Gemini API Key",
    value=config.GEMINI_API_KEY,
    type="password",
    help="Get a free key from https://aistudio.google.com/ to enable live Gemini AI.",
)
gemini_key = api_key_input.strip() or config.GEMINI_API_KEY

if api_key_input.strip() and api_key_input.strip() != config.GEMINI_API_KEY:
    if st.sidebar.button("💾 Save Key to .env", use_container_width=True):
        env_path = config.BASE_DIR / ".env"
        env_content = f"GEMINI_API_KEY={api_key_input.strip()}\nGEMINI_MODEL={config.DEFAULT_MODEL}\n"
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(env_content)
        st.sidebar.success("Key saved to .env!")
        st.rerun()

# Model selection
selected_model = st.sidebar.selectbox(
    "Gemini Model",
    options=config.AVAILABLE_MODELS,
    index=0,
    help="Gemini 2.5 Flash is recommended for ultra-fast classification.",
)

# Email fetch count
num_emails = st.sidebar.slider(
    "Unread Emails to Fetch",
    min_value=1,
    max_value=25,
    value=5 if demo_mode else 10,
    help="Number of unread inbox messages to retrieve.",
)

# Default reply tone
default_tone = st.sidebar.selectbox(
    "Default Reply Tone",
    options=config.TONES,
    index=0,
)

# Mark as read toggle
auto_mark_read = st.sidebar.checkbox(
    "Mark as Read after Send/Draft",
    value=False,
    help="Automatically remove the UNREAD label in Gmail once an action is taken.",
)

st.sidebar.divider()

# Instantiate Gmail Service
gmail_service = GmailService()

if demo_mode:
    st.sidebar.info("🧪 **Demo Mode Active**: Operating with realistic mock data.")
    current_user_email = "demo_user@example.com"
else:
    is_creds_present = gmail_service.is_credentials_file_present()
    is_authenticated = gmail_service.is_authenticated()

    if not is_creds_present:
        st.sidebar.error("⚠️ `credentials.json` not found in project folder.")
        current_user_email = "local_user@example.com"
    else:
        if is_authenticated:
            st.sidebar.success("✅ Gmail Connected")
            current_user_email = gmail_service.get_user_email() or "authenticated_user@gmail.com"
            if st.sidebar.button("Disconnect / Re-auth", use_container_width=True):
                gmail_service.disconnect()
                st.session_state.emails = []
                st.session_state.classifications = {}
                st.session_state.replies = {}
                st.session_state.action_status = {}
                st.rerun()
        else:
            current_user_email = "guest_user@example.com"
            st.sidebar.warning("🔑 Not logged into Gmail")
            if st.sidebar.button("Connect Gmail Account", use_container_width=True):
                try:
                    with st.spinner("Authorizing in your browser..."):
                        gmail_service.get_service()
                    st.sidebar.success("Connected successfully!")
                    st.rerun()
                except Exception as e:
                    st.sidebar.error(f"Authentication failed: {e}")

# Resolve current database user ID
current_user_id = db_manager.get_or_create_user(current_user_email) if is_db_connected else None

st.sidebar.divider()
st.sidebar.caption("Antigravity AI Email Workspace • v2.0")


# --- Main Dashboard Header ---
st.title("📬 AI Email Workspace")
st.markdown(
    "Automate incoming email triage, polish outgoing drafts, and maintain persistent audit analytics."
)

if not gemini_key and not demo_mode:
    st.warning("⚠️ Please provide a Gemini API Key in the sidebar or via `.env` to enable live AI classification.")

# --- Navigation Tabs ---
tab_inbox, tab_writer, tab_history = st.tabs([
    "📥 Inbox Triage & Auto-Reply",
    "✍️ AI Email Proofreader & Optimizer",
    "📊 History & Analytics",
])


# ==========================================
# TAB 1: INBOX TRIAGE & AUTO-RESPONDER
# ==========================================
with tab_inbox:
    # Action Bar: Fetch & Clear
    col_fetch, col_clear = st.columns([3, 1])

    with col_fetch:
        fetch_label = (
            f"🧪 Load & Analyze {min(num_emails, len(MOCK_EMAILS))} Sample Emails (Demo Mode)"
            if demo_mode
            else f"📥 Fetch & Analyze Latest {num_emails} Unread Emails"
        )
        fetch_btn = st.button(
            fetch_label,
            type="primary",
            disabled=(not demo_mode and not gmail_service.is_credentials_file_present()),
            use_container_width=True,
        )

    with col_clear:
        if st.button("🧹 Clear Inbox View", use_container_width=True):
            st.session_state.emails = []
            st.session_state.classifications = {}
            st.session_state.replies = {}
            st.session_state.action_status = {}
            st.rerun()

    # Logic: Fetch and Process Emails
    if fetch_btn:
        try:
            classifier = EmailClassifier(api_key=gemini_key, model=selected_model) if gemini_key else None
            responder = EmailResponder(api_key=gemini_key, model=selected_model) if gemini_key else None

            with st.status("Processing emails...", expanded=True) as status:
                if demo_mode:
                    status.write("Loading sample realistic emails...")
                    raw_emails = MOCK_EMAILS[:num_emails]
                else:
                    status.write("Connecting to Gmail API and fetching unread emails...")
                    raw_emails = gmail_service.fetch_unread_emails(max_results=num_emails)

                st.session_state.emails = raw_emails

                if not raw_emails:
                    status.update(label="No unread emails found!", state="complete")
                else:
                    progress_bar = st.progress(0)
                    total = len(raw_emails)

                    for idx, email_data in enumerate(raw_emails):
                        msg_id = email_data["id"]

                        # Check MySQL Cache first to save Gemini API calls!
                        cached_record = (
                            db_manager.get_saved_email(current_user_id, msg_id)
                            if current_user_id
                            else None
                        )

                        if cached_record and cached_record.get("category"):
                            status.write(
                                f"Loaded cached classification for: *{email_data['subject'][:40]}* (Saved Gemini API call)"
                            )
                            classification = EmailClassification(
                                category=cached_record["category"],
                                priority=cached_record["priority"],
                                confidence=float(cached_record["confidence"] or 0.9),
                                summary=cached_record["summary"],
                                action_needed=(cached_record["category"] in ["Work", "Urgent", "Personal"]),
                            )
                            st.session_state.classifications[msg_id] = classification
                            st.session_state.db_email_ids[msg_id] = cached_record["id"]

                            if cached_record.get("generated_reply"):
                                st.session_state.replies[msg_id] = cached_record["generated_reply"]
                        else:
                            status.write(
                                f"Classifying email {idx + 1}/{total}: *{email_data['subject'][:40]}*..."
                            )
                            if msg_id not in st.session_state.classifications:
                                if classifier and gemini_key:
                                    classification = classifier.classify_email(
                                        sender=email_data["sender"],
                                        subject=email_data["subject"],
                                        date=email_data["date"],
                                        body=email_data["body"],
                                    )
                                elif demo_mode:
                                    classification = MOCK_FALLBACK_CLASSIFICATIONS.get(
                                        msg_id,
                                        EmailClassification(
                                            category="Work",
                                            priority="Medium",
                                            confidence=0.9,
                                            summary="Sample business email for demonstration.",
                                            action_needed=True,
                                        ),
                                    )
                                else:
                                    classification = EmailClassification(
                                        category="Other",
                                        priority="Low",
                                        confidence=0.0,
                                        summary="No Gemini API key provided.",
                                        action_needed=False,
                                    )
                                st.session_state.classifications[msg_id] = classification

                            # Persist classification to MySQL
                            if current_user_id:
                                db_eid = db_manager.save_email(
                                    user_id=current_user_id,
                                    gmail_message_id=msg_id,
                                    sender=email_data["sender"],
                                    subject=email_data["subject"],
                                    category=classification.category,
                                    priority=classification.priority,
                                    confidence=classification.confidence,
                                    summary=classification.summary,
                                    received_at=email_data["date"],
                                )
                                st.session_state.db_email_ids[msg_id] = db_eid

                        # Draft reply
                        classification = st.session_state.classifications[msg_id]
                        should_reply = (
                            responder.should_reply(classification.category)
                            if responder
                            else (classification.category not in ["Spam", "Promotions"])
                        )

                        if msg_id not in st.session_state.replies and should_reply:
                            status.write(f"Drafting AI reply for: *{email_data['subject'][:40]}*...")
                            if responder and gemini_key:
                                draft_text = responder.generate_reply(
                                    sender=email_data["sender"],
                                    subject=email_data["subject"],
                                    body=email_data["body"],
                                    category=classification.category,
                                    tone=default_tone,
                                )
                            elif demo_mode:
                                draft_text = MOCK_FALLBACK_REPLIES.get(
                                    msg_id,
                                    f"Thank you for contacting us regarding {email_data['subject']}.\n\nBest,\n",
                                )
                            else:
                                draft_text = None

                            if draft_text:
                                st.session_state.replies[msg_id] = draft_text
                                db_eid = st.session_state.db_email_ids.get(msg_id)
                                if db_eid and current_user_id:
                                    db_manager.save_reply(
                                        email_id=db_eid,
                                        generated_reply=draft_text,
                                        tone=default_tone,
                                        status="draft",
                                    )

                        progress_bar.progress((idx + 1) / total)

                    status.update(label=f"Successfully analyzed {len(raw_emails)} emails!", state="complete")
            st.rerun()

        except Exception as exc:
            st.error(f"Error fetching or processing emails: {exc}")
            logger.exception("Failed during fetch/process run")

    # Stats & Metrics
    emails = st.session_state.emails
    classifications = st.session_state.classifications

    if emails:
        st.divider()

        total_count = len(emails)
        high_priority_count = sum(
            1 for m in emails if classifications.get(m["id"]) and classifications[m["id"]].priority == "High"
        )
        action_needed_count = sum(
            1 for m in emails if classifications.get(m["id"]) and classifications[m["id"]].action_needed
        )
        spam_promo_count = sum(
            1
            for m in emails
            if classifications.get(m["id"]) and classifications[m["id"]].category in ["Spam", "Promotions"]
        )

        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.metric("Total Fetched", total_count)
        with m2:
            st.metric("High Priority / Urgent", high_priority_count)
        with m3:
            st.metric("Action Required", action_needed_count)
        with m4:
            st.metric("Spam / Promo Filtered", spam_promo_count)

        cat_counts = {}
        for m in emails:
            c = classifications.get(m["id"])
            cat_name = c.category if c else "Unclassified"
            cat_counts[cat_name] = cat_counts.get(cat_name, 0) + 1

        chart_df = pd.DataFrame(
            list(cat_counts.items()),
            columns=["Category", "Count"],
        ).sort_values("Count", ascending=False)

        with st.expander("📊 Email Breakdown by Category", expanded=False):
            st.bar_chart(chart_df.set_index("Category"))

        st.divider()

        # Filters
        st.subheader("📥 Inbox Triage")
        f_col1, f_col2 = st.columns(2)

        with f_col1:
            selected_category_filter = st.multiselect(
                "Filter by Category",
                options=config.CATEGORIES,
                default=[],
                help="Leave empty to display all categories",
            )

        with f_col2:
            selected_priority_filter = st.multiselect(
                "Filter by Priority",
                options=config.PRIORITIES,
                default=[],
                help="Leave empty to display all priorities",
            )

        filtered_emails = []
        for email_item in emails:
            cls = classifications.get(email_item["id"])
            if not cls:
                filtered_emails.append(email_item)
                continue

            if selected_category_filter and cls.category not in selected_category_filter:
                continue
            if selected_priority_filter and cls.priority not in selected_priority_filter:
                continue

            filtered_emails.append(email_item)

        st.caption(f"Showing **{len(filtered_emails)}** of **{len(emails)}** emails")

        # Email List
        for email_item in filtered_emails:
            msg_id = email_item["id"]
            cls: Optional[EmailClassification] = classifications.get(msg_id)
            current_status = st.session_state.action_status.get(msg_id)
            db_eid = st.session_state.db_email_ids.get(msg_id)

            cat_tag = cls.category if cls else "Other"
            priority_tag = cls.priority if cls else "Medium"
            cat_color = config.CATEGORY_COLORS.get(cat_tag, "#6B7280")
            prio_color = config.PRIORITY_COLORS.get(priority_tag, "#F59E0B")

            status_prefix = f"[{current_status.upper()}] " if current_status else ""
            expander_title = (
                f"{status_prefix}[{cat_tag} • {priority_tag}] {email_item['sender']} — {email_item['subject']}"
            )

            with st.expander(expander_title, expanded=(cls and cls.priority == "High")):
                left_col, right_col = st.columns([1, 1], gap="medium")

                with left_col:
                    st.markdown("#### 📨 Email Details")
                    st.markdown(f"**From:** `{email_item['sender']}`")
                    st.markdown(f"**Subject:** {email_item['subject']}")
                    st.markdown(f"**Date:** {email_item['date']}")

                    if cls:
                        badge_html = f"""
                        <div style="margin: 8px 0;">
                            {render_badge(cls.category, cat_color)}
                            {render_badge(f"Priority: {cls.priority}", prio_color)}
                            {render_badge(f"Confidence: {int(cls.confidence * 100)}%", "#4B5563")}
                        </div>
                        """
                        st.markdown(badge_html, unsafe_allow_html=True)
                        st.markdown(
                            f'<div class="summary-box"><b>AI Summary:</b> {html.escape(cls.summary)}</div>',
                            unsafe_allow_html=True,
                        )

                    st.markdown("**Original Body:**")
                    st.markdown(
                        f'<div class="email-body-box">{html.escape(email_item["body"])}</div>',
                        unsafe_allow_html=True,
                    )

                with right_col:
                    st.markdown("#### ✍️ AI Auto-Responder")

                    if current_status:
                        st.info(f"Status: **{current_status}**")

                    if cls and cls.category in ["Spam", "Promotions"]:
                        st.warning(f"🚫 Auto-reply disabled for **{cls.category}** emails.")
                        if st.button("Mark as Skipped", key=f"skip_btn_{msg_id}"):
                            st.session_state.action_status[msg_id] = "Skipped"
                            if db_eid and current_user_id:
                                db_manager.update_reply_status(db_eid, "skipped")
                            st.rerun()
                    else:
                        tone_key = f"tone_select_{msg_id}"
                        email_tone = st.selectbox(
                            "Tone for this reply:",
                            options=config.TONES,
                            index=config.TONES.index(default_tone) if default_tone in config.TONES else 0,
                            key=tone_key,
                        )

                        instruction_key = f"instruction_{msg_id}"
                        custom_instruction = st.text_input(
                            "Additional instructions (optional):",
                            placeholder="e.g. Confirm meeting for Thursday at 3 PM",
                            key=instruction_key,
                        )

                        if st.button("🔄 Regenerate Reply", key=f"regen_{msg_id}"):
                            responder = EmailResponder(api_key=gemini_key, model=selected_model)
                            with st.spinner("Regenerating reply..."):
                                new_reply = responder.generate_reply(
                                    sender=email_item["sender"],
                                    subject=email_item["subject"],
                                    body=email_item["body"],
                                    category=cls.category if cls else "Other",
                                    tone=email_tone,
                                    custom_instruction=custom_instruction,
                                )
                                if new_reply:
                                    st.session_state.replies[msg_id] = new_reply
                                    if db_eid and current_user_id:
                                        db_manager.save_reply(
                                            email_id=db_eid,
                                            generated_reply=new_reply,
                                            tone=email_tone,
                                            status="draft",
                                        )
                                    st.rerun()

                        draft_val = st.session_state.replies.get(
                            msg_id,
                            "No draft generated yet. Click 'Regenerate Reply' to create one.",
                        )
                        draft_text = st.text_area(
                            "Review & Edit Draft Reply:",
                            value=draft_val,
                            height=180,
                            key=f"draft_text_{msg_id}",
                        )

                        b_col1, b_col2, b_col3 = st.columns(3)

                        # Save as Draft
                        with b_col1:
                            if st.button("💾 Save Draft", key=f"save_draft_{msg_id}", use_container_width=True):
                                try:
                                    if demo_mode:
                                        st.session_state.action_status[msg_id] = "Draft Saved (Demo Mode)"
                                        st.success("Draft saved (Demo Mode simulated)!")
                                    else:
                                        with st.spinner("Saving draft in Gmail..."):
                                            gmail_service.create_draft(
                                                thread_id=email_item["thread_id"],
                                                to=email_item["sender"],
                                                subject=email_item["subject"],
                                                body=draft_text,
                                                message_id_header=email_item.get("message_id_header"),
                                            )
                                            if auto_mark_read:
                                                gmail_service.mark_as_read(msg_id)
                                            st.session_state.action_status[msg_id] = "Draft Saved in Gmail"
                                            st.success("Draft saved in Gmail!")

                                    if db_eid and current_user_id:
                                        db_manager.save_reply(
                                            email_id=db_eid,
                                            generated_reply=draft_text,
                                            tone=email_tone,
                                            status="draft",
                                        )
                                        db_manager.update_reply_status(db_eid, "draft")

                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Failed to create draft: {e}")

                        # Send Reply
                        with b_col2:
                            confirm_send = st.checkbox(
                                "Confirm Send",
                                key=f"confirm_{msg_id}",
                                help="Safeguard: check this box to confirm sending directly.",
                            )
                            if st.button(
                                "🚀 Send Now",
                                key=f"send_btn_{msg_id}",
                                type="primary",
                                disabled=not confirm_send,
                                use_container_width=True,
                            ):
                                try:
                                    if demo_mode:
                                        st.session_state.action_status[msg_id] = "Sent to Recipient (Demo Mode)"
                                        st.success("Reply sent (Demo Mode simulated)!")
                                    else:
                                        with st.spinner("Sending reply via Gmail..."):
                                            gmail_service.send_reply(
                                                thread_id=email_item["thread_id"],
                                                to=email_item["sender"],
                                                subject=email_item["subject"],
                                                body=draft_text,
                                                message_id_header=email_item.get("message_id_header"),
                                            )
                                            if auto_mark_read:
                                                gmail_service.mark_as_read(msg_id)
                                            st.session_state.action_status[msg_id] = "Sent to Recipient"
                                            st.success("Reply sent successfully!")

                                    if db_eid and current_user_id:
                                        db_manager.save_reply(
                                            email_id=db_eid,
                                            generated_reply=draft_text,
                                            tone=email_tone,
                                            status="sent",
                                        )
                                        db_manager.update_reply_status(db_eid, "sent")

                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Failed to send: {e}")

                        # Skip button
                        with b_col3:
                            if st.button("⏭️ Skip", key=f"skip_action_{msg_id}", use_container_width=True):
                                st.session_state.action_status[msg_id] = "Skipped"
                                if db_eid and current_user_id:
                                    db_manager.update_reply_status(db_eid, "skipped")
                                st.rerun()

    else:
        st.info(
            "👋 Welcome! Connect your Gmail account using the sidebar and click "
            "**'Fetch & Analyze Latest Unread Emails'** to begin."
        )


# ==========================================
# TAB 2: AI EMAIL PROOFREADER & OPTIMIZER
# ==========================================
with tab_writer:
    st.subheader("✍️ AI Email Proofreader & Communication Coach")
    st.markdown(
        "Write or paste your email draft below. Gemini will **classify every mistake** "
        "(grammar, tone, clarity, missing info) and generate the **single best way to send it**."
    )

    sample_col1, sample_col2 = st.columns([2, 5])
    with sample_col1:
        if st.button("📝 Load Sample Rough Draft (Instant Test)", use_container_width=True):
            st.session_state.opt_recipient = SAMPLE_ROUGH_DRAFT["recipient"]
            st.session_state.opt_subject = SAMPLE_ROUGH_DRAFT["subject"]
            st.session_state.opt_body = SAMPLE_ROUGH_DRAFT["body"]
            st.session_state.opt_audience = SAMPLE_ROUGH_DRAFT["audience"]
            st.session_state.opt_tone = SAMPLE_ROUGH_DRAFT["tone"]
            st.session_state.opt_objective = SAMPLE_ROUGH_DRAFT["objective"]
            st.session_state.optimization_result = None
            st.session_state.opt_action_status = None
            st.rerun()

    with sample_col2:
        if st.session_state.optimization_result:
            if st.button("🧹 Clear Optimization Results", use_container_width=False):
                st.session_state.optimization_result = None
                st.session_state.opt_action_status = None
                st.rerun()

    # Form inputs
    input_c1, input_c2 = st.columns([1, 1], gap="medium")
    with input_c1:
        audience_options = [
            "Manager / Executive",
            "Client / Customer",
            "Colleague / Peer",
            "External Vendor / Partner",
            "Recruiter / Hiring Manager",
        ]
        tone_options = [
            "Professional",
            "Friendly & Warm",
            "Persuasive & Confident",
            "Short and direct",
            "Executive & Authoritative",
        ]

        opt_recipient = st.text_input(
            "Recipient Name / Email:",
            value=st.session_state.opt_recipient,
            placeholder="e.g. David (Engineering Director) or client@example.com",
            key="input_opt_recipient",
        )
        opt_audience = st.selectbox(
            "Target Audience:",
            options=audience_options,
            index=audience_options.index(st.session_state.opt_audience)
            if st.session_state.opt_audience in audience_options
            else 0,
            key="input_opt_audience",
        )
        opt_tone = st.selectbox(
            "Intended Tone:",
            options=tone_options,
            index=tone_options.index(st.session_state.opt_tone)
            if st.session_state.opt_tone in tone_options
            else 0,
            key="input_opt_tone",
        )

    with input_c2:
        opt_objective = st.text_input(
            "Main Goal / Purpose (Optional):",
            value=st.session_state.opt_objective,
            placeholder="e.g. Inform about delay without blaming others and propose realistic solutions",
            key="input_opt_objective",
        )
        opt_subject = st.text_input(
            "Draft Subject Line:",
            value=st.session_state.opt_subject,
            placeholder="e.g. quick question about the project deadline delay",
            key="input_opt_subject",
        )

    opt_body = st.text_area(
        "Draft Email Body (Write your raw thoughts here):",
        value=st.session_state.opt_body,
        height=180,
        placeholder="Type or paste your rough email here...",
        key="input_opt_body",
    )

    analyze_btn = st.button(
        "🚀 Analyze Mistakes & Optimize Email",
        type="primary",
        use_container_width=True,
    )

    # Process Analysis
    if analyze_btn:
        if not opt_body.strip() and not opt_subject.strip():
            st.warning("Please enter a subject or email body before optimizing.")
        else:
            try:
                optimizer = EmailOptimizer(api_key=gemini_key, model=selected_model)
                with st.spinner("Analyzing email tone, identifying mistakes, and generating the optimal draft..."):
                    result = optimizer.optimize_email(
                        recipient=opt_recipient,
                        subject=opt_subject,
                        body=opt_body,
                        target_tone=opt_tone,
                        target_audience=opt_audience,
                        objective=opt_objective,
                        use_mock_fallback=demo_mode,
                    )
                    st.session_state.optimization_result = result
                    st.session_state.opt_recipient = opt_recipient
                    st.session_state.opt_subject = opt_subject
                    st.session_state.opt_body = opt_body
                    st.session_state.opt_audience = opt_audience
                    st.session_state.opt_tone = opt_tone
                    st.session_state.opt_objective = opt_objective
                    st.session_state.opt_action_status = None
                st.rerun()
            except Exception as e:
                st.error(f"Optimization error: {e}")

    # Display Optimization Results
    res = st.session_state.optimization_result
    if res:
        st.divider()

        score_label = (
            "High Polish • Ready"
            if res.overall_score >= 80
            else ("Good • Minor Fixes Needed" if res.overall_score >= 60 else "Needs Major Polish")
        )

        score_col1, score_col2, score_col3 = st.columns([1, 2, 1])
        with score_col1:
            st.metric("Readiness Score", f"{res.overall_score}/100", delta=score_label)
        with score_col2:
            st.markdown("**Detected Impression:**")
            st.info(f"🎭 *{res.tone_detected}*")
        with score_col3:
            st.metric("Identified Issues", len(res.mistakes))

        st.subheader("🔍 Classified Mistakes & Weaknesses")
        if not res.mistakes:
            st.success("🎉 No significant mistakes found! Your draft is clear and well-crafted.")
        else:
            MISTAKE_TAG_COLORS = {
                "Grammar & Spelling": "#EF4444",
                "Tone & Politeness": "#F59E0B",
                "Clarity & Conciseness": "#3B82F6",
                "Missing Information": "#8B5CF6",
            }
            SEV_COLORS = {
                "Critical": "#DC2626",
                "Moderate": "#D97706",
                "Minor": "#6B7280",
            }

            for idx, m in enumerate(res.mistakes):
                tag_col = MISTAKE_TAG_COLORS.get(m.category, "#6B7280")
                sev_col = SEV_COLORS.get(m.severity, "#6B7280")

                st.markdown(
                    f"""
                    <div class="mistake-card">
                        <div style="margin-bottom: 6px;">
                            {render_badge(m.category, tag_col)}
                            {render_badge(f"Severity: {m.severity}", sev_col)}
                        </div>
                        <div class="mistake-quote"><b>Original Text:</b> "{html.escape(m.original_fragment)}"</div>
                        <div style="font-size: 0.92rem; margin: 4px 0;"><b>Why this is an issue:</b> {html.escape(m.explanation)}</div>
                        <div class="suggestion-box"><b>Recommended Fix:</b> {html.escape(m.suggested_fix)}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        st.divider()
        st.subheader("⚖️ Before vs. After Comparison")
        comp_c1, comp_c2 = st.columns(2, gap="large")

        with comp_c1:
            st.markdown("#### ❌ Original Draft")
            st.markdown(f"**Subject:** {st.session_state.opt_subject or '(None)'}")
            st.markdown(
                f'<div class="email-body-box" style="border-left: 3px solid #EF4444;">{html.escape(st.session_state.opt_body or "(Empty)")}</div>',
                unsafe_allow_html=True,
            )

        with comp_c2:
            st.markdown("#### ✨ The Best Way to Send (Optimized)")
            st.markdown(f"**Subject:** {res.optimized_subject}")
            st.markdown(
                f'<div class="email-body-box" style="border-left: 3px solid #10B981;">{html.escape(res.optimized_body)}</div>',
                unsafe_allow_html=True,
            )

        if res.key_improvements:
            st.markdown("#### 💡 Key Enhancements Made:")
            for imp in res.key_improvements:
                st.markdown(f"- {imp}")

        st.divider()
        st.subheader("📤 Take Action on Optimized Email")

        if st.session_state.opt_action_status:
            st.success(f"Status: **{st.session_state.opt_action_status}**")

        final_subj = st.text_input(
            "Final Subject Line to Send/Draft:",
            value=res.optimized_subject,
            key="final_opt_subject",
        )
        final_body = st.text_area(
            "Final Email Body (Editable before sending):",
            value=res.optimized_body,
            height=220,
            key="final_opt_body",
        )

        action_c1, action_c2 = st.columns([1, 1])

        with action_c1:
            if st.button("💾 Save as Gmail Draft", key="opt_save_draft_btn", use_container_width=True):
                try:
                    if demo_mode:
                        st.session_state.opt_action_status = "Optimized Draft Saved (Demo Mode simulated)"
                        st.success("Draft saved successfully (Demo Mode simulated)!")
                        st.rerun()
                    else:
                        with st.spinner("Saving draft in Gmail..."):
                            gmail_service.create_draft(
                                thread_id="",
                                to=st.session_state.opt_recipient,
                                subject=final_subj,
                                body=final_body,
                            )
                            st.session_state.opt_action_status = "Draft Saved in Gmail"
                            st.success("Draft created in Gmail!")
                            st.rerun()
                except Exception as e:
                    st.error(f"Error saving draft: {e}")

        with action_c2:
            confirm_opt_send = st.checkbox(
                "Confirm Send Optimized Email",
                key="confirm_opt_send",
                help="Safeguard: check to enable sending directly.",
            )
            if st.button(
                "🚀 Send via Gmail",
                key="opt_send_btn",
                type="primary",
                disabled=not confirm_opt_send,
                use_container_width=True,
            ):
                try:
                    if demo_mode:
                        st.session_state.opt_action_status = "Optimized Email Sent (Demo Mode simulated)"
                        st.success("Email sent successfully (Demo Mode simulated)!")
                        st.rerun()
                    else:
                        with st.spinner("Sending email via Gmail..."):
                            gmail_service.send_reply(
                                thread_id="",
                                to=st.session_state.opt_recipient,
                                subject=final_subj,
                                body=final_body,
                            )
                            st.session_state.opt_action_status = "Email Sent to Recipient"
                            st.success("Email sent successfully!")
                            st.rerun()
                except Exception as e:
                    st.error(f"Error sending email: {e}")


# ==========================================
# TAB 3: HISTORY & ANALYTICS (MYSQL PERSISTENCE)
# ==========================================
with tab_history:
    st.subheader("📊 Triage History & Performance Analytics")

    # Privacy Guarantee Banner
    st.markdown(
        """
        <div class="privacy-notice">
            🔒 <b>Privacy Notice</b>: Full email bodies and OAuth tokens are <b>never stored in the database</b>.
            Only sender, subject, AI classification taxonomy, priority, confidence score, and concise summaries are preserved.
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not is_db_connected:
        st.info(
            "ℹ️ **MySQL Database Not Connected**: To enable persistent history tracking, "
            "duplicate Gemini API call skipping, and long-term analytics, please configure your "
            "Aiven MySQL credentials in your `.env` file or Streamlit Cloud secrets."
        )
        with st.expander("🛠️ How to configure Aiven MySQL"):
            st.markdown(
                """
                1. Sign up for a free tier MySQL database at [Aiven.io](https://console.aiven.io/).
                2. Download the `ca.pem` certificate into your project directory.
                3. Update `.env` with:
                   ```env
                   DB_HOST=your-service.aivencloud.com
                   DB_PORT=your_port
                   DB_USER=avnadmin
                   DB_PASSWORD=your_password
                   DB_NAME=defaultdb
                   DB_SSL_CA=ca.pem
                   ```
                4. Run `python test_db.py` to verify the connection.
                """
            )
    else:
        # Load Stats from Database
        stats = db_manager.get_stats(current_user_id) if current_user_id else {}

        total_saved = stats.get("total_emails", 0)
        status_counts = stats.get("status_counts", {})
        drafted_count = status_counts.get("draft", 0)
        sent_count = status_counts.get("sent", 0)
        skipped_count = status_counts.get("skipped", 0)

        # Overview Metrics
        h_col1, h_col2, h_col3, h_col4 = st.columns(4)
        with h_col1:
            st.metric("Total Emails Triaged", total_saved)
        with h_col2:
            st.metric("Drafts Saved", drafted_count)
        with h_col3:
            st.metric("Replies Sent", sent_count)
        with h_col4:
            st.metric("Skipped Items", skipped_count)

        st.divider()

        # Analytics Charts: Emails by Category Over Time
        timeline_data = stats.get("timeline", [])
        if timeline_data:
            st.subheader("📈 Triage Volume by Category Over Time")
            t_df = pd.DataFrame(timeline_data)
            # Pivot table for clean area/bar visualization
            pivot_df = t_df.pivot_table(
                index="triage_date",
                columns="category",
                values="count",
                fill_value=0,
            )
            st.bar_chart(pivot_df)
            st.divider()

        # Historical Email Records Table
        st.subheader("📑 Triage History Log")

        filter_c1, filter_c2 = st.columns([1, 1])
        with filter_c1:
            hist_cat = st.selectbox(
                "Filter History by Category:",
                options=["All"] + config.CATEGORIES,
                key="hist_cat_filter",
            )
        with filter_c2:
            hist_limit = st.slider(
                "Rows to display:",
                min_value=10,
                max_value=200,
                value=50,
                key="hist_limit_slider",
            )

        hist_records = db_manager.get_history(
            user_id=current_user_id,
            limit=hist_limit,
            category=hist_cat if hist_cat != "All" else None,
        )

        if not hist_records:
            st.info("No saved email triage records found in the database yet. Fetch emails in the Inbox tab to start building your log!")
        else:
            table_rows = []
            for r in hist_records:
                table_rows.append(
                    {
                        "Date": str(r.get("created_at"))[:16],
                        "From": r.get("sender"),
                        "Subject": r.get("subject"),
                        "Category": r.get("category"),
                        "Priority": r.get("priority"),
                        "Summary": r.get("summary"),
                        "Reply Status": (r.get("reply_status") or "None").upper(),
                    }
                )

            st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)

        # Danger Zone: User Data Deletion (GDPR / Privacy Compliance)
        st.divider()
        st.subheader("⚠️ Privacy & Data Management")
        st.caption("Under strict privacy controls, you can permanently erase all your stored triage records from the database at any time.")

        with st.expander("🗑️ Delete My Data (Irreversible)"):
            st.warning("This will permanently delete all your stored emails, classifications, and reply logs from the cloud database.")
            confirm_delete = st.checkbox(
                "I understand this action cannot be undone and permanently erases my records.",
                key="confirm_delete_box",
            )
            if st.button("Permanently Erase All My Data", type="primary", disabled=not confirm_delete):
                if current_user_id:
                    success = db_manager.delete_user_data(current_user_id)
                    if success:
                        st.session_state.emails = []
                        st.session_state.classifications = {}
                        st.session_state.replies = {}
                        st.session_state.db_email_ids = {}
                        st.success("All your stored records have been permanently erased from the database.")
                        st.rerun()
                    else:
                        st.error("Failed to delete records. Please try again.")
