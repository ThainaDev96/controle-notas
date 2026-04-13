from pathlib import Path
from decouple import config
import os

# Diretório base do projeto
BASE_DIR = Path(__file__).resolve().parent.parent

# Chave secreta do Django
SECRET_KEY = config('DJANGO_SECRET_KEY')

# Modo de debug (False em produção!)
DEBUG = config('DJANGO_DEBUG', default=False, cast=bool)

# Hosts permitidos
ALLOWED_HOSTS = []

# Aplicações instaladas
INSTALLED_APPS = [
    "django.contrib.admin",         # Interface de administração
    "django.contrib.auth",          # Sistema de autenticação e permissões
    "django.contrib.contenttypes",  # Relações genéricas entre modelos
    "django.contrib.sessions",      # Suporte a sessões de usuário
    "django.contrib.messages",      # Sistema de mensagens temporárias (flash messages)
    "django.contrib.staticfiles",   # Gerenciamento de arquivos estáticos (CSS, JS, imagens)
]

# Middlewares
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",            # Segurança básica
    "django.contrib.sessions.middleware.SessionMiddleware",     # Habilita sessões
    "django.middleware.common.CommonMiddleware",                # Funcionalidades gerais (redirecionamento www, etc.)
    "django.middleware.csrf.CsrfViewMiddleware",                # Proteção contra CSRF
    "django.contrib.auth.middleware.AuthenticationMiddleware",  # Associa o usuário autenticado à requisição
    "django.contrib.messages.middleware.MessageMiddleware",     # Sistema de mensagens (flash messages)
    "django.middleware.clickjacking.XFrameOptionsMiddleware",   # Proteção contra clickjacking
]

# Configuração de URLs e WSGI
ROOT_URLCONF = "core.urls"
WSGI_APPLICATION = "core.wsgi.application"

# Templates
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / 'templates'],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# Banco de dados
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("POSTGRES_DB"),
        "USER": config("POSTGRES_USER"),
        "PASSWORD": config("POSTGRES_PASSWORD"),
        "HOST": config("POSTGRES_HOST"),
        "PORT": config("POSTGRES_PORT"),
    }
}
# Tipo padrão de chave primária
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Validações de senha
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Internacionalização
LANGUAGE_CODE = "pt-br"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Arquivos estáticos
STATIC_URL = "static/"
