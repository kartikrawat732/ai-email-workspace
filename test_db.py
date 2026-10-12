"""Aiven for MySQL Connection Verification Script.

Tests SSL connection, retrieves MySQL server version, and checks table readiness.
Usage:
    python test_db.py
"""

import sys
import mysql.connector
from mysql.connector import Error

import config
from database import DatabaseManager


def run_test():
    print("=" * 50)
    print("🔍 Testing Aiven for MySQL Connection with SSL")
    print("=" * 50)

    # 1. Inspect configuration
    print(f"Host:       {config.DB_HOST or '(Not set in .env)'}")
    print(f"Port:       {config.DB_PORT}")
    print(f"User:       {config.DB_USER or '(Not set in .env)'}")
    print(f"Database:   {config.DB_NAME}")

    ca_path = config.get_ssl_ca_path()
    print(f"SSL CA:     {ca_path or 'None (ca.pem not found in root)'}")
    print("-" * 50)

    if not config.DB_HOST or not config.DB_USER:
        print("❌ Error: DB_HOST or DB_USER is missing in your .env file.")
        print("Please configure .env with your Aiven MySQL credentials before testing.")
        sys.exit(1)

    connection_args = {
        "host": config.DB_HOST,
        "port": config.DB_PORT,
        "user": config.DB_USER,
        "password": config.DB_PASSWORD,
        "database": config.DB_NAME,
        "connect_timeout": 10,
    }

    if ca_path:
        connection_args["ssl_ca"] = ca_path
        connection_args["ssl_verify_cert"] = True
    else:
        connection_args["ssl_disabled"] = False
        print("⚠️ Warning: ca.pem not detected. Attempting SSL connection without certificate validation...")

    try:
        conn = mysql.connector.connect(**connection_args)
        if conn.is_connected():
            cursor = conn.cursor(dictionary=True)

            # Query server version & user
            cursor.execute("SELECT VERSION() AS ver, CURRENT_USER() AS cur_user, DATABASE() AS cur_db")
            row = cursor.fetchone()

            print("✅ Connected successfully!")
            print(f"MySQL Version:  {row['ver']}")
            print(f"Connected User: {row['cur_user']}")
            print(f"Database Name:  {row['cur_db']}")
            print("-" * 50)

            # Check existing tables
            cursor.execute("SHOW TABLES")
            tables = [list(t.values())[0] for t in cursor.fetchall()]
            expected = ["users", "emails", "replies", "settings"]
            missing = [t for t in expected if t not in tables]

            if tables:
                print(f"Existing tables in '{config.DB_NAME}': {', '.join(tables)}")

            if missing:
                print(f"⚠️ Tables not yet initialized: {', '.join(missing)}")
                print("Running database.py init_db() to automatically apply schema.sql...")
                db = DatabaseManager()
                ok, msg = db.init_db()
                if ok:
                    print(f"✅ {msg}")
                else:
                    print(f"❌ Failed to run schema: {msg}")
            else:
                print("✅ All required tables (users, emails, replies, settings) are ready!")

            cursor.close()
            conn.close()
            print("=" * 50)
            print("🎉 Database setup verified and ready for Streamlit!")
            print("=" * 50)

    except Error as err:
        print("\n❌ Connection Failed:")
        print(f"Error Code:    {err.errno}")
        print(f"Error Message: {err.msg}")
        print("\nTroubleshooting Tips:")
        print("1. Verify your Aiven service status is 'RUNNING' in the Aiven Console.")
        print("2. Check that DB_HOST, DB_PORT, DB_USER, and DB_PASSWORD in .env match your Aiven Overview tab.")
        print("3. Ensure 'ca.pem' is downloaded from Aiven and placed in the project folder.")
        sys.exit(1)


if __name__ == "__main__":
    run_test()
