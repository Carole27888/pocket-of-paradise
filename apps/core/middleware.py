"""Keeps the current user available to model save() so created_by/updated_by fill automatically."""
import threading

_local = threading.local()


def get_current_user():
    user = getattr(_local, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return user
    return None


class CurrentUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _local.user = getattr(request, "user", None)
        try:
            return self.get_response(request)
        finally:
            _local.user = None
