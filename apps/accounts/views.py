import base64
import io

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import roles, twofactor
from .forms import LoginForm, ProfileForm, UserForm
from .models import User
from .permissions import role_required

WINDOW = 15 * 60


def _client_ip(request):
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (fwd.split(",")[0] if fwd else request.META.get("REMOTE_ADDR", "")).strip()


BACKEND = "django.contrib.auth.backends.ModelBackend"


def _finish_login(request, user, remember: bool):
    login(request, user, backend=getattr(user, "backend", BACKEND))
    if not remember:
        request.session.set_expiry(0)


def two_factor_challenge(request):
    """Second step of sign-in: a 6-digit authenticator code or a one-time recovery code."""
    pending = request.session.get("pending_2fa")
    if not pending:
        return redirect("accounts:login")
    user = get_object_or_404(User, pk=pending["uid"], is_active=True)
    key = f"2fa-fail:{user.pk}"
    error = ""
    if request.method == "POST":
        from apps.system.services import audit

        if cache.get(key, 0) >= settings.LOGIN_RATE_LIMIT:
            error = "Too many attempts. Wait 15 minutes and sign in again."
            request.session.pop("pending_2fa", None)
        else:
            code = request.POST.get("code", "").strip()
            counter = twofactor.verify(user.totp_secret, code, user.totp_last_counter)
            rec = twofactor.hash_code(code)
            if counter:
                user.totp_last_counter = counter
                user.save(update_fields=["totp_last_counter"])
                ok = True
            elif rec in (user.recovery_hashes or []):
                user.recovery_hashes = [h for h in user.recovery_hashes if h != rec]
                user.save(update_fields=["recovery_hashes"])
                audit(user, "2fa.recovery_used", user, f"{len(user.recovery_hashes)} recovery codes left", request)
                ok = True
            else:
                ok = False
            if ok:
                cache.delete(key)
                request.session.pop("pending_2fa", None)
                user.backend = BACKEND
                _finish_login(request, user, pending.get("remember", False))
                return redirect(pending.get("next") or "core:home")
            cache.set(key, cache.get(key, 0) + 1, WINDOW)
            audit(user, "2fa.failed", user, "Wrong code at sign-in", request)
            error = "That code isn't right. Codes change every 30 seconds."
    return render(request, "registration/two_factor.html", {"error": error, "email": user.email})


@login_required
def profile(request):
    """My Profile: contact and payroll details. A change to pay method, bank, M-PESA or KRA PIN is recorded and flagged to Finance."""
    from apps.notifications.services import notify_roles
    from apps.system.services import audit

    request.current_module = "me"
    from .forms import AlertsForm

    emp = getattr(request.user, "employee", None)
    if request.method == "POST" and request.POST.get("action") == "alerts":
        alerts = AlertsForm(request.POST, instance=request.user, prefix="al")
        if alerts.is_valid():
            alerts.save()
            messages.success(request, "Alert settings saved.")
            return redirect("accounts:profile")
        return render(request, "accounts/profile.html", {"form": ProfileForm(instance=emp) if emp else None, "alerts": alerts})
    alerts = AlertsForm(instance=request.user, prefix="al")
    if emp is None:
        return render(request, "accounts/profile.html", {"form": None, "alerts": alerts})
    form = ProfileForm(request.POST or None, instance=emp)
    if request.method == "POST" and form.is_valid():
        changed = [f for f in ProfileForm.PAYROLL if f in form.changed_data]
        form.save()
        if emp.user_id and emp.full_name != request.user.full_name:
            request.user.full_name = emp.full_name
            request.user.save(update_fields=["full_name"])
        if changed:
            labels = ", ".join(form.fields[f].label for f in changed)
            audit(request.user, "profile.payroll_changed", emp, detail=f"Changed: {labels}", request=request)
            notify_roles([roles.FINANCE], f"{emp.full_name} changed payroll details", f"Changed: {labels}. Please confirm before the next payroll run.", "", "warning",
                         dedupe_key=f"payroll-change:{emp.pk}:{request.user.pk}")
        messages.success(request, "Profile saved." + (" Finance has been told about the payroll changes." if changed else ""))
        return redirect("accounts:profile")
    return render(request, "accounts/profile.html", {"form": form, "alerts": alerts})


@login_required
def security(request):
    """My Space → Security: set up, regenerate recovery codes, or turn off two-factor sign-in."""
    from apps.system.services import audit

    user = request.user
    request.current_module = "me"
    required = twofactor.requires_2fa(user)
    ctx = {"required": required, "enabled": user.totp_enabled, "feature_on": twofactor.feature_on(), "recovery_left": len(user.recovery_hashes or [])}
    action = request.POST.get("action") if request.method == "POST" else ""
    if action == "enable":
        secret = request.session.get("totp_setup")
        counter = twofactor.verify(secret, request.POST.get("code", "")) if secret else None
        if counter:
            plain, hashes = twofactor.new_recovery_codes()
            user.totp_secret, user.totp_enabled, user.totp_last_counter, user.recovery_hashes = secret, True, counter, hashes
            user.save(update_fields=["totp_secret", "totp_enabled", "totp_last_counter", "recovery_hashes"])
            request.session.pop("totp_setup", None)
            audit(user, "2fa.enabled", user, request=request)
            return render(request, "accounts/security.html", {**ctx, "enabled": True, "codes": plain, "recovery_left": len(plain)})
        messages.error(request, "That code isn't right. Check the time on your phone and try again.")
    elif action in ("disable", "regenerate") and user.totp_enabled:
        counter = twofactor.verify(user.totp_secret, request.POST.get("code", ""), user.totp_last_counter)
        if not counter:
            messages.error(request, "Enter a current code from your authenticator app to confirm.")
        elif action == "disable":
            if required:
                messages.error(request, "Your role requires two-factor sign-in, so it can't be turned off.")
            else:
                user.totp_secret, user.totp_enabled, user.recovery_hashes = "", False, []
                user.save(update_fields=["totp_secret", "totp_enabled", "recovery_hashes"])
                audit(user, "2fa.disabled", user, request=request)
                messages.success(request, "Two-factor sign-in turned off.")
                return redirect("accounts:security")
        else:
            plain, hashes = twofactor.new_recovery_codes()
            user.recovery_hashes, user.totp_last_counter = hashes, counter
            user.save(update_fields=["recovery_hashes", "totp_last_counter"])
            audit(user, "2fa.codes_regenerated", user, request=request)
            return render(request, "accounts/security.html", {**ctx, "codes": plain, "recovery_left": len(plain)})
    if not user.totp_enabled and twofactor.feature_on():
        secret = request.session.get("totp_setup") or twofactor.new_secret()
        request.session["totp_setup"] = secret
        import qrcode
        import qrcode.image.svg

        buf = io.BytesIO()
        qrcode.make(twofactor.otpauth_uri(user.email, secret), image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2).save(buf)
        ctx.update(secret=secret, qr=base64.b64encode(buf.getvalue()).decode())
    return render(request, "accounts/security.html", ctx)


@require_POST
@role_required(roles.ADMIN)
def reset_two_factor(request, pk):
    """Admin helper for a locked-out person: clears their 2FA so they must enrol again."""
    from apps.system.services import audit

    u = get_object_or_404(User, pk=pk)
    u.totp_secret, u.totp_enabled, u.recovery_hashes, u.totp_last_counter = "", False, [], 0
    u.save(update_fields=["totp_secret", "totp_enabled", "recovery_hashes", "totp_last_counter"])
    audit(request.user, "2fa.reset", u, "Reset by an administrator", request)
    messages.success(request, f"Two-factor sign-in reset for {u.full_name}. They will set it up again at next sign-in.")
    return redirect("accounts:users")


def login_view(request):
    if request.user.is_authenticated:
        return redirect("core:home")
    form = LoginForm(request, request.POST or None)
    locked = False
    if request.method == "POST":
        key = f"login-fail:{_client_ip(request)}:{request.POST.get('email', '').lower()}"
        fails = cache.get(key, 0)
        if fails >= settings.LOGIN_RATE_LIMIT:
            locked = True
            form.add_error(None, "Too many attempts. Wait 15 minutes and try again.")
        elif form.is_valid():
            cache.delete(key)
            nxt = request.GET.get("next") or request.POST.get("next") or ""
            if not url_has_allowed_host_and_scheme(nxt, {request.get_host()}, request.is_secure()):
                nxt = ""
            if twofactor.feature_on() and form.user.totp_enabled:      # password is right: now ask for the code
                request.session["pending_2fa"] = {"uid": form.user.pk, "remember": bool(form.cleaned_data.get("remember")), "next": nxt}
                return redirect("accounts:two_factor")
            _finish_login(request, form.user, bool(form.cleaned_data.get("remember")))
            return redirect(nxt or "core:home")
        else:
            cache.set(key, fails + 1, WINDOW)
    return render(request, "registration/login.html", {"form": form, "locked": locked})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("accounts:login")


@role_required(roles.ADMIN)
def users(request):
    qs = User.objects.prefetch_related("groups").select_related("employee").order_by("full_name")
    return render(request, "accounts/users.html", {"users": qs, "role_descriptions": roles.ROLE_DESCRIPTIONS})


@role_required(roles.ADMIN)
def user_edit(request, pk=None):
    obj = get_object_or_404(User, pk=pk) if pk else None
    form = UserForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        was = sorted(obj.groups.values_list("name", flat=True)) if obj else []
        u = form.save()
        from apps.system.services import audit

        audit(request.user, "user.created" if obj is None else "user.saved", u,
              f"Roles: {', '.join(sorted(u.groups.values_list('name', flat=True))) or '—'}" + (f" (was {', '.join(was) or '—'})" if obj else ""), request)
        messages.success(request, f"{u.full_name} saved.")
        return redirect("accounts:users")
    return render(request, "components/form_page.html",
                  {"form": form, "title": f"Edit {obj}" if obj else "New user", "cancel_url": "/accounts/users/"})


@login_required
def install(request):
    import qrcode
    import qrcode.image.svg

    url = settings.SITE_URL + "/?source=pwa"
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    svg_b64 = base64.b64encode(buf.getvalue()).decode()
    request.current_module = "admin" if request.user.has_role(roles.ADMIN) else "operations"
    return render(request, "accounts/install.html", {"qr_svg_b64": svg_b64, "site_url": settings.SITE_URL})
