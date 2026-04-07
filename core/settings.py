import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.getenv('TJ_RMM_SECRET_KEY', 'django-insecure-change-this-in-production')
DEBUG = os.getenv('TJ_RMM_DEBUG', 'true').lower() == 'true'

# CRITICAL: Allow all hosts for local WiFi testing
ALLOWED_HOSTS = ['*'] if DEBUG else os.getenv('TJ_RMM_ALLOWED_HOSTS', '').split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'agents',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'core.urls'
WSGI_APPLICATION = 'core.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# Security settings
SECURE_SSL_REDIRECT = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = 'DENY'

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'agents',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'core.urls'
WSGI_APPLICATION = 'core.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

TIME_ZONE = 'Africa/Johannesburg'
USE_TZ = True

STATIC_URL = 'static/'
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

AGENT_KEY = "TJ-RMM-SECRET-2026"
CSRF_FAILURE_VIEW = "agents.views.csrf_failure"

DEFAULT_FROM_EMAIL = os.getenv("TJ_RMM_DEFAULT_FROM_EMAIL", "alerts@tj-rmm.local")
EMAIL_BACKEND = os.getenv(
    "TJ_RMM_EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend",
)
EMAIL_HOST = os.getenv("TJ_RMM_EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.getenv("TJ_RMM_EMAIL_PORT", "25"))
EMAIL_HOST_USER = os.getenv("TJ_RMM_EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("TJ_RMM_EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("TJ_RMM_EMAIL_USE_TLS", "false").lower() == "true"
EMAIL_USE_SSL = os.getenv("TJ_RMM_EMAIL_USE_SSL", "false").lower() == "true"
CLIENT_PORTAL_BASE_URL = os.getenv("TJ_RMM_CLIENT_PORTAL_BASE_URL", "http://127.0.0.1:8000")
STRIPE_PUBLISHABLE_KEY = os.getenv("TJ_RMM_STRIPE_PUBLISHABLE_KEY", "")
STRIPE_SECRET_KEY = os.getenv("TJ_RMM_STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("TJ_RMM_STRIPE_WEBHOOK_SECRET", "")
STRIPE_SUCCESS_URL = os.getenv(
    "TJ_RMM_STRIPE_SUCCESS_URL",
    CLIENT_PORTAL_BASE_URL + "/client/billing/?session_id={CHECKOUT_SESSION_ID}",
)
STRIPE_CANCEL_URL = os.getenv("TJ_RMM_STRIPE_CANCEL_URL", CLIENT_PORTAL_BASE_URL + "/client/billing/")
STRIPE_CURRENCY = os.getenv("TJ_RMM_STRIPE_CURRENCY", "usd").lower()

SUPPORT_NOTIFICATION_EMAILS = [
    email.strip()
    for email in os.getenv("TJ_RMM_SUPPORT_NOTIFICATION_EMAILS", "").split(",")
    if email.strip()
]
