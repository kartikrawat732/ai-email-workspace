"""Aiven for MySQL cloud database integration layer.

Provides connection pooling with SSL verification, parameterized execution,
duplicate Gemini API call skipping, user data isolation, and graceful
fallback when offline.
"""

from contextlib import contextmanager
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import mysql.connector
from mysql.connector import Error, errorcode
from mysql.connector.pooling import MySQLConnectionPool

import config

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages MySQL connection pooling, schema initialization, and transactional queries."""

    def __init__(self):
        """Initialize connection pool settings."""
        self._pool: Optional[MySQLConnectionPool] = None
        self._is_initialized = False

    def _create_pool(self) -> Optional[MySQLConnectionPool]:
        """Establish the thread-safe MySQL connection pool with SSL."""
        if not config.is_database_configured():
            logger.info("Database host or user not configured. Running in offline/memory mode.")
            return None

        ca_path = config.get_ssl_ca_path()

        connection_args = {
            "host": config.DB_HOST,
            "port": config.DB_PORT,
            "user": config.DB_USER,
            "password": config.DB_PASSWORD,
            "database": config.DB_NAME,
            "autocommit": True,
            "connect_timeout": 10,
        }

        # Aiven requires SSL; configure CA verification if available
        if ca_path:
            connection_args["ssl_ca"] = ca_path
            connection_args["ssl_verify_cert"] = True
            logger.info("Using Aiven SSL CA Certificate: %s", ca_path)
        else:
            # Fallback SSL flag if CA certificate file is missing
            connection_args["ssl_disabled"] = False
            logger.warning(
                "Aiven CA certificate file not found. Attempting SSL connection without CA verification."
            )

        try:
            pool = MySQLConnectionPool(
                pool_name="email_assistant_pool",
                pool_size=5,
                pool_reset_session=True,
                **connection_args,
            )
            logger.info("MySQL connection pool established successfully.")
            return pool
        except Error as err:
            logger.error("Failed to initialize MySQL connection pool: %s", err)
            return None

    def get_pool(self) -> Optional[MySQLConnectionPool]:
        """Retrieve or lazy-load the active connection pool."""
        if self._pool is None:
            self._pool = self._create_pool()
        return self._pool

    @contextmanager
    def get_connection(self):
        """Context manager yielding a pooled MySQL connection.

        Yields:
            MySQL connection object.

        Raises:
            ConnectionError: If pool is unavailable or exhausted.
        """
        pool = self.get_pool()
        if not pool:
            raise ConnectionError("MySQL connection pool is not available. Please verify your credentials.")

        conn = None
        try:
            conn = pool.get_connection()
            yield conn
        except Error as err:
            logger.error("MySQL connection error: %s", err)
            raise err
        finally:
            if conn and conn.is_connected():
                conn.close()

    def check_connection(self) -> Tuple[bool, str]:
        """Test database connection health and return diagnostic status.

        Returns:
            Tuple of (is_connected: bool, message: str).
        """
        if not config.is_database_configured():
            return False, "Database not configured (DB_HOST / DB_USER empty in .env or st.secrets)."

        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT VERSION()")
                    version = cursor.fetchone()
                    ver_str = version[0] if version else "Unknown"
                    return True, f"Connected to Aiven MySQL (v{ver_str}) with SSL"
        except Error as err:
            return False, f"Connection failed: {err.msg}"
        except Exception as exc:
            return False, f"Connection failed: {str(exc)}"

    def init_db(self) -> Tuple[bool, str]:
        """Execute schema.sql if tables are missing."""
        if not config.is_database_configured():
            return False, "Database credentials missing."

        schema_path = config.BASE_DIR / "schema.sql"
        if not schema_path.exists():
            return False, "schema.sql file not found."

        try:
            with open(schema_path, "r", encoding="utf-8") as f:
                schema_sql = f.read()

            # Strip SQL comments to avoid breaking on semicolons inside comments
            clean_lines = [
                line for line in schema_sql.splitlines()
                if not line.strip().startswith("--")
            ]
            clean_sql = "\n".join(clean_lines)

            # Execute statements
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    statements = [
                        stmt.strip() for stmt in clean_sql.split(";") if stmt.strip()
                    ]
                    for stmt in statements:
                        cursor.execute(stmt)
            self._is_initialized = True
            logger.info("Database schema initialized successfully.")
            return True, "Database schema verified and tables ready."
        except Error as err:
            logger.error("Error executing schema.sql: %s", err)
            return False, f"Schema initialization error: {err.msg}"

    # -------------------------------------------------------------
    # User Management
    # -------------------------------------------------------------
    def get_or_create_user(self, email: str, name: str = "") -> Optional[int]:
        """Retrieve existing user ID or create a new user record.

        Args:
            email: User's primary email address.
            name: Optional display name.

        Returns:
            User ID integer or None if database is offline.
        """
        if not config.is_database_configured() or not email:
            return None

        try:
            with self.get_connection() as conn:
                with conn.cursor(dictionary=True) as cursor:
                    cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
                    row = cursor.fetchone()
                    if row:
                        return row["id"]

                    # Insert new user
                    cursor.execute(
                        "INSERT INTO users (email, name) VALUES (%s, %s)",
                        (email, name or ""),
                    )
                    return cursor.lastrowid
        except Exception as exc:
            logger.error("Error in get_or_create_user: %s", exc)
            return None

    # -------------------------------------------------------------
    # Email Persistence & Cache Check
    # -------------------------------------------------------------
    def get_saved_email(
        self, user_id: int, gmail_message_id: str
    ) -> Optional[Dict[str, Any]]:
        """Check if an email was already classified to avoid redundant Gemini API calls.

        Args:
            user_id: Database user ID.
            gmail_message_id: Gmail message unique identifier.

        Returns:
            Dictionary with cached classification or None.
        """
        if not user_id or not gmail_message_id:
            return None

        try:
            with self.get_connection() as conn:
                with conn.cursor(dictionary=True) as cursor:
                    query = """
                        SELECT e.id, e.category, e.priority, e.confidence, e.summary, e.received_at,
                               r.generated_reply, r.tone, r.status AS reply_status
                        FROM emails e
                        LEFT JOIN replies r ON r.email_id = e.id
                        WHERE e.user_id = %s AND e.gmail_message_id = %s
                        LIMIT 1
                    """
                    cursor.execute(query, (user_id, gmail_message_id))
                    return cursor.fetchone()
        except Exception as exc:
            logger.error("Error checking saved email cache: %s", exc)
            return None

    def save_email(
        self,
        user_id: int,
        gmail_message_id: str,
        sender: str,
        subject: str,
        category: str,
        priority: str,
        confidence: float,
        summary: str,
        received_at: str = "",
    ) -> Optional[int]:
        """Save email metadata and classification (body is never stored).

        Args:
            user_id: User database ID.
            gmail_message_id: Unique Gmail message ID.
            sender: Sender email or name.
            subject: Subject line.
            category: Classified category.
            priority: Assigned priority.
            confidence: Confidence score float.
            summary: Short summary text.
            received_at: Date string.

        Returns:
            Inserted email record ID, or None if error.
        """
        if not user_id:
            return None

        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    query = """
                        INSERT INTO emails (
                            user_id, gmail_message_id, sender, subject,
                            category, priority, confidence, summary, received_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            category = VALUES(category),
                            priority = VALUES(priority),
                            confidence = VALUES(confidence),
                            summary = VALUES(summary)
                    """
                    cursor.execute(
                        query,
                        (
                            user_id,
                            gmail_message_id,
                            sender[:255] if sender else "Unknown",
                            subject[:500] if subject else "(No Subject)",
                            category,
                            priority,
                            float(confidence or 0.0),
                            summary or "",
                            received_at[:100] if received_at else "",
                        ),
                    )
                    # Retrieve the inserted or existing row ID
                    cursor.execute(
                        "SELECT id FROM emails WHERE user_id = %s AND gmail_message_id = %s",
                        (user_id, gmail_message_id),
                    )
                    row = cursor.fetchone()
                    return row[0] if row else None
        except Exception as exc:
            logger.error("Error saving email to database: %s", exc)
            return None

    # -------------------------------------------------------------
    # Reply Persistence
    # -------------------------------------------------------------
    def save_reply(
        self,
        email_id: int,
        generated_reply: str,
        tone: str = "Professional",
        status: str = "draft",
    ) -> Optional[int]:
        """Save a generated AI reply draft.

        Args:
            email_id: Database email record ID.
            generated_reply: Draft response content.
            tone: Tone used for draft.
            status: Status ('draft', 'sent', 'skipped').

        Returns:
            Inserted reply ID or None.
        """
        if not email_id or not generated_reply:
            return None

        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    # Check if a reply record already exists for this email
                    cursor.execute("SELECT id FROM replies WHERE email_id = %s", (email_id,))
                    existing = cursor.fetchone()
                    if existing:
                        cursor.execute(
                            """
                            UPDATE replies
                            SET generated_reply = %s, tone = %s, status = %s
                            WHERE email_id = %s
                            """,
                            (generated_reply, tone, status, email_id),
                        )
                        return existing[0]

                    cursor.execute(
                        """
                        INSERT INTO replies (email_id, generated_reply, tone, status)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (email_id, generated_reply, tone, status),
                    )
                    return cursor.lastrowid
        except Exception as exc:
            logger.error("Error saving reply: %s", exc)
            return None

    def update_reply_status(self, email_id: int, status: str) -> bool:
        """Update the dispatch status of an email reply.

        Args:
            email_id: Database email record ID.
            status: 'draft', 'sent', or 'skipped'.

        Returns:
            True if updated successfully.
        """
        if not email_id or status not in ["draft", "sent", "skipped"]:
            return False

        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "UPDATE replies SET status = %s WHERE email_id = %s",
                        (status, email_id),
                    )
                    return True
        except Exception as exc:
            logger.error("Error updating reply status: %s", exc)
            return False

    # -------------------------------------------------------------
    # History & Analytics Queries
    # -------------------------------------------------------------
    def get_history(
        self,
        user_id: int,
        limit: int = 50,
        category: Optional[str] = None,
        priority: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch historical triaged emails for the user.

        Args:
            user_id: Database user ID.
            limit: Maximum rows to return.
            category: Optional category filter.
            priority: Optional priority filter.

        Returns:
            List of email dictionaries with reply status.
        """
        if not user_id:
            return []

        try:
            with self.get_connection() as conn:
                with conn.cursor(dictionary=True) as cursor:
                    query = """
                        SELECT e.id AS email_id, e.gmail_message_id, e.sender, e.subject,
                               e.category, e.priority, e.confidence, e.summary, e.received_at,
                               e.created_at, r.generated_reply, r.tone, r.status AS reply_status
                        FROM emails e
                        LEFT JOIN replies r ON r.email_id = e.id
                        WHERE e.user_id = %s
                    """
                    params: List[Any] = [user_id]

                    if category:
                        query += " AND e.category = %s"
                        params.append(category)
                    if priority:
                        query += " AND e.priority = %s"
                        params.append(priority)

                    query += " ORDER BY e.created_at DESC LIMIT %s"
                    params.append(limit)

                    cursor.execute(query, tuple(params))
                    return cursor.fetchall()
        except Exception as exc:
            logger.error("Error fetching email history: %s", exc)
            return []

    def get_stats(self, user_id: int) -> Dict[str, Any]:
        """Aggregate statistical metrics for the user's triaged emails.

        Args:
            user_id: Database user ID.

        Returns:
            Dict containing total counts, breakdown by category, priority, and timeline.
        """
        stats: Dict[str, Any] = {
            "total_emails": 0,
            "category_counts": {},
            "priority_counts": {},
            "status_counts": {},
            "timeline": [],
        }

        if not user_id:
            return stats

        try:
            with self.get_connection() as conn:
                with conn.cursor(dictionary=True) as cursor:
                    # 1. Total emails
                    cursor.execute(
                        "SELECT COUNT(*) AS total FROM emails WHERE user_id = %s",
                        (user_id,),
                    )
                    row = cursor.fetchone()
                    stats["total_emails"] = row["total"] if row else 0

                    # 2. Category counts
                    cursor.execute(
                        """
                        SELECT category, COUNT(*) AS count
                        FROM emails
                        WHERE user_id = %s
                        GROUP BY category
                        ORDER BY count DESC
                        """,
                        (user_id,),
                    )
                    stats["category_counts"] = {
                        r["category"]: r["count"] for r in cursor.fetchall()
                    }

                    # 3. Priority counts
                    cursor.execute(
                        """
                        SELECT priority, COUNT(*) AS count
                        FROM emails
                        WHERE user_id = %s
                        GROUP BY priority
                        """,
                        (user_id,),
                    )
                    stats["priority_counts"] = {
                        r["priority"]: r["count"] for r in cursor.fetchall()
                    }

                    # 4. Reply status counts
                    cursor.execute(
                        """
                        SELECT r.status, COUNT(*) AS count
                        FROM replies r
                        JOIN emails e ON r.email_id = e.id
                        WHERE e.user_id = %s
                        GROUP BY r.status
                        """,
                        (user_id,),
                    )
                    stats["status_counts"] = {
                        r["status"]: r["count"] for r in cursor.fetchall()
                    }

                    # 5. Timeline (emails triaged per day by category)
                    cursor.execute(
                        """
                        SELECT DATE(created_at) AS triage_date, category, COUNT(*) AS count
                        FROM emails
                        WHERE user_id = %s
                        GROUP BY DATE(created_at), category
                        ORDER BY triage_date ASC
                        """,
                        (user_id,),
                    )
                    stats["timeline"] = cursor.fetchall()

            return stats
        except Exception as exc:
            logger.error("Error calculating stats: %s", exc)
            return stats

    # -------------------------------------------------------------
    # User Privacy: Delete Data
    # -------------------------------------------------------------
    def delete_user_data(self, user_id: int) -> bool:
        """Permanently delete all data associated with the user.

        Cascades across emails, replies, and settings.

        Args:
            user_id: User database ID.

        Returns:
            True if deleted successfully.
        """
        if not user_id:
            return False

        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
                    logger.info("Successfully deleted all records for user %s", user_id)
                    return True
        except Exception as exc:
            logger.error("Error deleting user data: %s", exc)
            return False
