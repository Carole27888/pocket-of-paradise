from django.shortcuts import redirect

from . import twofactor

ALLOWED = ("/system/security/", "/accounts/security/", "/accounts/logout/", "/accounts/login/", "/static/", "/sw.js", "/manifest", "/offline/", "/favicon")


class TwoFactorSetupMiddleware:
    """When 2FA is on, roles that must use it are sent to the setup page until they have enrolled."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        u = getattr(request, "user", None)
        if u is not None and twofactor.requires_2fa(u) and not u.totp_enabled and not request.path.startswith(ALLOWED):
            return redirect("accounts:security")
        return self.get_response(request)
