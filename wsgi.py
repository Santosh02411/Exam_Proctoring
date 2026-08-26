"""Production WSGI entry point — used by Gunicorn/uWSGI instead of run.py's
Flask dev server. Example: gunicorn --bind 0.0.0.0:8000 wsgi:app
"""
from app import create_app

app = create_app()
