from functools import wraps

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied

from apps.core.modules import user_can_access_module


def pick_module(request, keys):
    """The first module in `keys` the user may open; screens shared by two modules open in the one the user came
    from (Sales pages link into the shared enquiry screens), else the first allowed."""
    allowed = [k for k in keys if user_can_access_module(request.user, k)]
    if not allowed:
        return None
    match = getattr(request, "resolver_match", None)
    ns = match.namespace if match else ""
    if ns in allowed:
        return ns
    ref = request.META.get("HTTP_REFERER", "")
    for k in allowed:
        if f"/{k}/" in ref:
            return k
    return allowed[0]


def module_required(module_key: str, *shared_with: str):
    """Allow a view for `module_key` or any module in `shared_with` (shared record screens)."""
    keys = (module_key, *shared_with)

    def deco(view):
        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            chosen = pick_module(request, keys)
            if chosen is None:
                raise PermissionDenied
            request.current_module = getattr(request, "current_module", None) or chosen
            return view(request, *args, **kwargs)

        return wrapped

    return deco


def role_required(*names):
    def deco(view):
        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.has_role(*names):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapped

    return deco


class ModuleMixin(LoginRequiredMixin):
    module_key = "operations"
    shared_with: tuple = ()  # other modules that may open this (read-only shared record) screen
    edit_required = False
    # User property that grants editing. The default also requires opening the screen from its home module, so
    # Sales users stay read-only on catalogue records; screens that override it (clients) work from any shared module.
    edit_permission = "can_edit_operations"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        chosen = pick_module(request, (self.module_key, *self.shared_with))
        if chosen is None:
            raise PermissionDenied
        if self.edit_required:
            allowed = getattr(request.user, self.edit_permission)
            if self.edit_permission == "can_edit_operations":
                allowed = allowed and chosen == self.module_key
            if not allowed:
                raise PermissionDenied
        request.current_module = chosen
        return super().dispatch(request, *args, **kwargs)


def is_scoped_agent(user) -> bool:
    """A sales agent who may only see their own enquiries, clients, calls, quotations and bookings."""
    from . import roles

    return user.has_role(roles.SALES_AGENT) and not user.has_role(
        roles.ADMIN, roles.DIRECTOR, roles.SALES_MANAGER, roles.RESERVATIONS, roles.FRONTDESK_MANAGER,
        roles.RECEPTIONIST, roles.OPERATIONS, roles.MARKETING)


def is_marketing_scoped(user) -> bool:
    """Marketing staff see only leads that came in through the social platforms (and never contact details)."""
    from . import roles

    return user.has_role(roles.MARKETING) and not user.is_superuser and not user.has_role(
        roles.ADMIN, roles.DIRECTOR, roles.COO, roles.SALES_MANAGER, roles.FRONTDESK_MANAGER, roles.RESERVATIONS, roles.RECEPTIONIST, roles.OPERATIONS)
