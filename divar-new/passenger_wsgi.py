# passenger_wsgi.py
# برای اجرای Flask روی cPanel با Passenger

import sys
import os

# مسیر پروژه را به sys.path اضافه کن
APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

# اگر virtualenv داری، این بخش را از حالت توضیح خارج کن و مسیر را درست کن:
# VENV_PATH = os.path.join(APP_DIR, "venv")
# activate = os.path.join(VENV_PATH, "bin", "activate_this.py")
# if os.path.exists(activate):
#     with open(activate) as f:
#         exec(f.read(), {"__file__": activate})

# بعضی هاست‌ها نیاز به این دارند
os.environ.setdefault("FLASK_ENV", "production")

from app import app as application