# config.py

TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID = ""

NOTIFY_EMAIL = "arminaali.aaa@gmail.com"
SMTP_HOST = "localhost"
SMTP_PORT = 587
SMTP_USER = "app@didastyar.ir"
SMTP_PASS = "Armin@2001"
SMTP_USE_SSL = False

DATABASE_PATH = "/home/znxnjrpm/didastyar.ir/divar.db"  # اگر پوشه موجود نباشد خودکار ساخته می‌شود؛ در غیر این صورت کنار app.py: divar.db
MAX_NOTIFY_PER_RUN = 12
REQUEST_DELAY = 1.0

# ---- New settings ----
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "12345678alpha"

TRIAL_DAYS = 3                     # legacy (kept for compatibility)
TRIAL_HOURS = 3                    # trial length for new signups (hours) — جلوگیری از سوءاستفاده
SEARCH_COOLDOWN_SECONDS = 4        # per-user cooldown between /search calls
MAX_PARALLEL_DIVAR_REQUESTS = 5    # concurrency cap for Divar API calls