"""Encrypted-at-rest text field (Fernet) for passport numbers and third-party tokens."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models


def _fernet() -> Fernet:
    key = settings.FIELD_ENCRYPTION_KEY
    if not key:
        # Derive a stable key from SECRET_KEY so dev works out of the box.
        key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest()).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


class EncryptedCharField(models.TextField):
    PREFIX = "enc$"

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value in (None, ""):
            return value
        if isinstance(value, str) and value.startswith(self.PREFIX):
            return value
        return self.PREFIX + _fernet().encrypt(value.encode()).decode()

    def from_db_value(self, value, expression, connection):
        return self.decrypt(value)

    def to_python(self, value):
        return self.decrypt(value)

    @classmethod
    def decrypt(cls, value):
        if not value or not isinstance(value, str) or not value.startswith(cls.PREFIX):
            return value
        try:
            return _fernet().decrypt(value[len(cls.PREFIX):].encode()).decode()
        except InvalidToken:
            return ""
