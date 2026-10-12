"""Configuration settings for the AI Email Classifier & Auto-Responder.

Loads environment variables, defines Gmail OAuth scopes, file paths,
category taxonomies, UI styling mappings, and MySQL database credentials.
Supports dual resolution: st.secrets on Streamlit Community Cloud with
fallback to .env for local execution.
"""

import os
from pathlib import Path
import tempfile
from typing import List, Dict, Optional
from dotenv import load_dotenv

# Load local environment variables from .env file
load_dotenv()

# Base project directory
BASE_DIR: Path = Path(__file__).resolve().parent


def get_config_value(key: str, default: str = "") -> str:
    """Retrieve a configuration value from st.secrets first, falling back to os.getenv.

    Args:
        key: Configuration variable name.
        default: Fallback default value if not found.

    Returns:
        The resolved string value.
    """
    try:
        import streamlit as st
        # Check flat key in st.secrets
        if hasattr(st, "secrets") and key in st.secrets:
            val = st.secrets[key]
            if val is not None:
                return str(val).strip()
        # Check nested [database] or [mysql] tables if present
        if hasattr(st, "secrets"):
            for section in ["database", "mysql"]:
                if section in st.secrets and key in st.secrets[section]:
                    val = st.secrets[section][key]
                    if val is not None:
                        return str(val).strip()
    except Exception:
        # st.secrets may raise when called outside a Streamlit runtime
        pass

    return os.getenv(key, default).strip()


# Gmail OAuth file paths
CREDENTIALS_FILE: Path = BASE_DIR / "credentials.json"
TOKEN_FILE: Path = BASE_DIR / "token.json"

# Gmail API Scopes
GMAIL_SCOPES: List[str] = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
]

# Google Gemini API Settings
GEMINI_API_KEY: str = get_config_value("GEMINI_API_KEY", "")
DEFAULT_MODEL: str = get_config_value("GEMINI_MODEL", "gemini-2.5-flash")
AVAILABLE_MODELS: List[str] = ["gemini-2.5-flash", "gemini-2.5-pro"]

# Database Configuration (Aiven for MySQL Cloud)
DB_HOST: str = get_config_value("DB_HOST", "")
DB_PORT: int = int(get_config_value("DB_PORT", "3306") or "3306")
DB_USER: str = get_config_value("DB_USER", "")
DB_PASSWORD: str = get_config_value("DB_PASSWORD", "")
DB_NAME: str = get_config_value("DB_NAME", "defaultdb")
DB_SSL_CA: str = get_config_value("DB_SSL_CA", "ca.pem")
DB_SSL_CA_CONTENT: str = get_config_value("DB_SSL_CA_CONTENT", "")

# Temporary cache for written SSL certificate path
_cached_ssl_ca_path: Optional[str] = None


def get_ssl_ca_path() -> Optional[str]:
    """Resolve the path to the Aiven MySQL SSL CA certificate.

    Checks:
    1. Local file path (e.g. ca.pem in project root).
    2. Streamlit Cloud: If DB_SSL_CA_CONTENT is provided as text in st.secrets,
       writes it to a secure runtime temporary file and returns its path.

    Returns:
        Absolute path to CA certificate file, or None if unavailable.
    """
    global _cached_ssl_ca_path
    if _cached_ssl_ca_path and os.path.exists(_cached_ssl_ca_path):
        return _cached_ssl_ca_path

    # Check local file path
    candidate_path = Path(DB_SSL_CA)
    if not candidate_path.is_absolute():
        candidate_path = BASE_DIR / candidate_path

    if candidate_path.exists() and candidate_path.is_file():
        _cached_ssl_ca_path = str(candidate_path)
        return _cached_ssl_ca_path

    # Check in-memory content from Streamlit Cloud secrets
    if DB_SSL_CA_CONTENT:
        try:
            temp_cert = tempfile.NamedTemporaryFile(
                delete=False, suffix=".pem", mode="w", encoding="utf-8"
            )
            temp_cert.write(DB_SSL_CA_CONTENT)
            temp_cert.flush()
            temp_cert.close()
            _cached_ssl_ca_path = temp_cert.name
            return _cached_ssl_ca_path
        except Exception:
            pass

    return None


def is_database_configured() -> bool:
    """Return True if minimal required database host and user are specified."""
    return bool(DB_HOST and DB_USER)


# Classification Taxonomies
CATEGORIES: List[str] = [
    "Work",
    "Personal",
    "Spam",
    "Promotions",
    "Finance",
    "Urgent",
    "Other",
]

PRIORITIES: List[str] = ["High", "Medium", "Low"]

# Response Tone Options
TONES: List[str] = [
    "Professional",
    "Friendly",
    "Short and direct",
]

# Color mappings for UI category pills
CATEGORY_COLORS: Dict[str, str] = {
    "Work": "#2563EB",       # Royal Blue
    "Personal": "#7C3AED",   # Purple
    "Finance": "#059669",    # Emerald Green
    "Urgent": "#DC2626",     # Crimson Red
    "Promotions": "#D97706", # Amber
    "Spam": "#4B5563",       # Dark Slate
    "Other": "#6B7280",      # Cool Gray
}

# Color mappings for priority badges
PRIORITY_COLORS: Dict[str, str] = {
    "High": "#EF4444",    # Vibrant Red
    "Medium": "#F59E0B",  # Warm Orange
    "Low": "#10B981",     # Soft Green
}
