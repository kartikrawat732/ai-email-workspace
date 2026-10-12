-- ========================================================
-- AI Email Classifier & Auto-Responder Database Schema
-- Compatible with MySQL 8.0+ and Aiven for MySQL Cloud
-- ========================================================

-- 1. Users Table
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(255) DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2. Emails Table (Metadata & AI Classification only; email bodies are never stored)
CREATE TABLE IF NOT EXISTS emails (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    gmail_message_id VARCHAR(255) NOT NULL,
    sender VARCHAR(255) NOT NULL,
    subject VARCHAR(500) DEFAULT '',
    category VARCHAR(50) NOT NULL,
    priority VARCHAR(20) NOT NULL,
    confidence FLOAT DEFAULT 0.0,
    summary TEXT,
    received_at VARCHAR(100) DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_user_gmail_msg UNIQUE (user_id, gmail_message_id),
    CONSTRAINT fk_emails_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_user_category (user_id, category),
    INDEX idx_user_created (user_id, created_at),
    INDEX idx_priority (priority)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3. Replies Table (Draft responses and user dispatch status)
CREATE TABLE IF NOT EXISTS replies (
    id INT AUTO_INCREMENT PRIMARY KEY,
    email_id INT NOT NULL,
    generated_reply TEXT,
    tone VARCHAR(50) DEFAULT 'Professional',
    status ENUM('draft', 'sent', 'skipped') DEFAULT 'draft',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_replies_email FOREIGN KEY (email_id) REFERENCES emails(id) ON DELETE CASCADE,
    INDEX idx_email_status (email_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 4. Settings Table (Per-user preferences)
CREATE TABLE IF NOT EXISTS settings (
    user_id INT PRIMARY KEY,
    default_tone VARCHAR(50) DEFAULT 'Professional',
    num_emails INT DEFAULT 10,
    model_name VARCHAR(100) DEFAULT 'gemini-2.5-flash',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_settings_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
