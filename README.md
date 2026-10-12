# 📬 AI Email Workspace with Aiven MySQL Persistence

An intelligent email management web app built with **Python 3.10+**, **Streamlit**, **Gmail API (OAuth 2.0)**, **Google Gemini 2.5**, and cloud-hosted **Aiven for MySQL**.

---

## 🚀 Features

1. **📥 Inbox Triage & Auto-Responder**:
   - Fetches unread Gmail messages securely.
   - Categorizes incoming emails into *Work*, *Personal*, *Spam*, *Promotions*, *Finance*, *Urgent*, and *Other*.
   - Generates contextual draft replies tailored by tone (*Professional*, *Friendly*, *Short & direct*).
   - Skips Spam and Promotions automatically.
   - **Cost Optimization**: Caches classifications in MySQL to avoid paying for redundant Gemini API calls.
2. **✍️ AI Email Proofreader & Optimizer**:
   - Classifies user draft mistakes across *Grammar & Spelling*, *Tone & Politeness*, *Clarity & Conciseness*, and *Missing Information*.
   - Generates the single best, ready-to-send version of the email with before/after comparisons.
3. **📊 History & Analytics (Aiven MySQL with SSL)**:
   - Tracks historical triage metrics and category breakdown over time.
   - Strict privacy safeguards: **Email bodies and OAuth tokens are never stored in the database.**
   - One-click **"Delete My Data"** button for complete data erasure.

---

## 🛠️ Step 1: Manual Setup Steps (Aiven MySQL)

### 1. Create a Free Aiven for MySQL Service
1. Sign up for a free account at [console.aiven.io](https://console.aiven.io/).
2. Click **Create service** > Select **MySQL**.
3. Choose the **Free Plan** (or Hobbyist tier) and pick your preferred cloud region.
4. Click **Create service** and wait 2–3 minutes until the service status changes to **RUNNING**.

### 2. Download the SSL CA Certificate (`ca.pem`)
1. In the Aiven Console, go to your service's **Overview** tab.
2. Under the **Connection parameters** box, locate **CA Certificate**.
3. Click **Download CA certificate** and save the file as `ca.pem` directly in this project's root folder:
   ```
   c:\Users\karti\OneDrive\Documents\ai_email\ca.pem
   ```

### 3. Run `schema.sql` in Your MySQL IDE
You can execute [`schema.sql`](schema.sql) through any of the following:
- **Aiven Web Console**: Under your service, navigate to **Databases** > query editor and paste the contents of `schema.sql`.
- **DBeaver / MySQL Workbench**: Connect using the Host, Port, User, and Password from your Aiven Overview tab (with SSL enabled pointing to `ca.pem`), then run `schema.sql`.
- **Automatic Setup**: Our Python script `test_db.py` will also offer to run `schema.sql` automatically for you upon first connection!

---

## ⚙️ Step 2: Configure Environment Variables

Copy `.env.example` to `.env`:
```powershell
Copy-Item .env.example .env
```

Open `.env` and fill in your credentials from Aiven and Google AI Studio:
```env
# Google Gemini API Key (from https://aistudio.google.com/)
GEMINI_API_KEY=AIzaSy...

# Aiven for MySQL Cloud Configuration
DB_HOST=mysql-xxxxxxxx-your-account.aivencloud.com
DB_PORT=12345
DB_USER=avnadmin
DB_PASSWORD=your_aiven_password
DB_NAME=defaultdb
DB_SSL_CA=ca.pem
```

---

## 🧪 Step 3: Test Database Connection

Verify your connection and SSL configuration by running:
```powershell
python test_db.py
```
Expected output:
```
🔍 Testing Aiven for MySQL Connection with SSL
✅ Connected successfully!
MySQL Version:  8.0.xx
Connected User: avnadmin@...
Database Name:  defaultdb
✅ All required tables (users, emails, replies, settings) are ready!
```

---

## 🌐 Step 4: Streamlit Community Cloud Deployment

When deploying to Streamlit Community Cloud (where you cannot upload a physical `ca.pem` file), our app supports reading the certificate text directly from Streamlit Secrets!

In your Streamlit Cloud app dashboard, navigate to **Settings** > **Secrets** and paste:

```toml
GEMINI_API_KEY = "AIzaSy..."
DB_HOST = "mysql-xxxxxxxx-your-account.aivencloud.com"
DB_PORT = 12345
DB_USER = "avnadmin"
DB_PASSWORD = "your_aiven_password"
DB_NAME = "defaultdb"

# Paste the raw text of your ca.pem certificate here:
DB_SSL_CA_CONTENT = """-----BEGIN CERTIFICATE-----
MII...
...your certificate contents...
-----END CERTIFICATE-----"""
```
The app will automatically write this certificate string into a secure temporary runtime file and establish the SSL connection.

---

## 🏃 Running the Application Locally

```powershell
python -m streamlit run app.py
```
Open `http://localhost:8501` in your browser.
