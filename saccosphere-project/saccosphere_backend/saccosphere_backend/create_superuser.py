import os
import django
from django.contrib.auth import get_user_model

# Setup Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "saccosphere_backend.settings")
django.setup()

User = get_user_model()
username = "Yevisah"           # <--- This will be your username
email = "isaack.jyevisa@gmail.com"
password = "password123"       # <--- This will be your password

# Check if user exists, if not, create it
if not User.objects.filter(username=username).exists():
    print("Creating superuser...")
    User.objects.create_superuser(username, email, password)
    print("Superuser created successfully!")
else:
    print("Superuser already exists.")