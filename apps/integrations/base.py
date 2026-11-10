"""Service-layer pattern for third-party integrations.

Each integration has an abstract interface, a Mock implementation (default, realistic fake data)
and a Live implementation that reads keys from the environment. `get_service("fx")` returns the
right one based on INTEGRATIONS_MODE (or a per-service override like FX_MODE=live).
"""
import os

from django.conf import settings

from .models import IntegrationLog

_REGISTRY: dict[str, dict[str, type]] = {}


def register(name: str, mode: str):
    def deco(cls):
        _REGISTRY.setdefault(name, {})[mode] = cls
        cls.service_name = name
        cls.mode = mode
        return cls

    return deco


def _keys_saved(name: str) -> bool:
    """Keys entered under Administration → Integration Keys switch that service live without touching environment variables."""
    try:
        from apps.system.services import get_credential

        field = {"sms": "api_key", "whatsapp": "access_token"}.get(name)
        return bool(field and get_credential(name, field))
    except Exception:  # database not ready (migrations, first boot)
        return False


def get_service(name: str):
    mode = os.environ.get(f"{name.upper()}_MODE") or ("live" if _keys_saved(name) else settings.INTEGRATIONS_MODE)
    impls = _REGISTRY[name]
    cls = impls.get(mode) or impls["mock"]
    return cls()


class BaseService:
    service_name = ""
    mode = "mock"

    def log(self, action: str, ok: bool = True, detail: str = ""):
        IntegrationLog.objects.create(service=self.service_name, action=action, mode=self.mode, ok=ok,
                                      detail=detail[:4000])
