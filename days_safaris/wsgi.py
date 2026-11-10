import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "days_safaris.settings.prod")
application = get_wsgi_application()
