from rest_framework.permissions import BasePermission

from apps.core.modules import user_can_access_module


class ModuleAccess(BasePermission):
    """The API honours the same module access as the web app: set `required_modules` on a view (any one suffices)."""

    message = "Your role can't use this part of the API."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        mods = getattr(view, "required_modules", ())
        return not mods or any(user_can_access_module(user, m) for m in mods)
