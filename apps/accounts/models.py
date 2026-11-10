from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.core.fields import EncryptedCharField

from . import roles


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create(self, email, password, **extra):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.update(is_staff=True, is_superuser=True)
        return self._create(email, password, **extra)

    def get_by_natural_key(self, username):
        return self.get(**{f"{self.model.USERNAME_FIELD}__iexact": username})


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False, help_text="Can open the Django admin.")
    date_joined = models.DateTimeField(default=timezone.now)
    last_module = models.CharField(max_length=30, blank=True)
    totp_secret = EncryptedCharField(blank=True)
    totp_enabled = models.BooleanField(default=False)
    totp_last_counter = models.BigIntegerField(default=0)
    recovery_hashes = models.JSONField(default=list, blank=True)
    whatsapp_number = models.CharField("WhatsApp number", max_length=20, blank=True,
                                       help_text="Alerts and reminders are sent here on WhatsApp. Leave blank to use the phone number on the staff record.")
    NOTIFY_WHATSAPP, NOTIFY_EMAIL, NOTIFY_BOTH, NOTIFY_APP = "whatsapp", "email", "both", "app"
    NOTIFY_CHOICES = [(NOTIFY_WHATSAPP, "WhatsApp"), (NOTIFY_EMAIL, "E-mail"), (NOTIFY_BOTH, "WhatsApp and e-mail"), (NOTIFY_APP, "In the app only")]
    notify_via = models.CharField("Send my alerts by", max_length=8, choices=NOTIFY_CHOICES, default=NOTIFY_WHATSAPP,
                                  help_text="Every alert also shows in the app (the bell) whatever you choose here.")

    objects = UserManager()
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        ordering = ["full_name"]

    def __str__(self):
        return self.full_name or self.email

    @property
    def whatsapp_target(self) -> str:
        """The number WhatsApp alerts go to: the one on the profile, else the staff record's phone."""
        if self.whatsapp_number:
            return self.whatsapp_number
        emp = getattr(self, "employee", None)
        return (emp.phone if emp else "") or ""

    @property
    def wants_whatsapp(self) -> bool:
        return self.notify_via in (self.NOTIFY_WHATSAPP, self.NOTIFY_BOTH)

    @property
    def wants_email(self) -> bool:
        return self.notify_via in (self.NOTIFY_EMAIL, self.NOTIFY_BOTH)

    # ---- role helpers
    @property
    def role_names(self) -> set[str]:
        if not hasattr(self, "_role_cache"):
            self._role_cache = set(self.groups.values_list("name", flat=True))
        return self._role_cache

    def has_role(self, *names: str) -> bool:
        if self.is_superuser:
            return True
        return bool(self.role_names.intersection(names))

    @property
    def primary_role(self) -> str:
        if self.is_superuser and not self.role_names:
            return roles.ADMIN
        for r in roles.ALL_ROLES:
            if r in self.role_names:
                return r
        return "Staff"

    @property
    def initials(self) -> str:
        parts = (self.full_name or self.email).replace(".", " ").split()
        return ("".join(p[0] for p in parts[:2]) or "?").upper()

    @property
    def is_read_only(self) -> bool:
        return self.has_role(roles.DIRECTOR) and not self.has_role(*roles.EDITORS)

    @property
    def can_edit_operations(self) -> bool:
        return self.has_role(*roles.EDITORS)

    @property
    def can_edit_packages(self) -> bool:
        """Only the Sales Manager, Operations, COO and Admin add or change tour packages."""
        return self.has_role(*roles.PACKAGE_EDITORS)

    @property
    def can_manage_staff(self) -> bool:
        return self.has_role(roles.ADMIN, roles.COO, roles.OPERATIONS)

    @property
    def can_run_desk(self) -> bool:
        """Front Desk actions: capture from the queues, check guides and vehicles out and in."""
        return self.has_role(*roles.DESK_OPERATORS)

    @property
    def can_distribute_leads(self) -> bool:
        """Hand leads to agents: Front Desk, Sales Manager, COO and CEO only."""
        return self.is_superuser or self.has_role(*roles.LEAD_DISTRIBUTORS)

    @property
    def can_manage_finance(self) -> bool:
        return self.has_role(*roles.FINANCE_EDITORS)

    @property
    def can_manage_suppliers(self) -> bool:
        return self.has_role(roles.ADMIN, roles.COO, roles.SUPPLIER_COORDINATOR, roles.FINANCE)

    @property
    def can_manage_trips(self) -> bool:
        return self.has_role(roles.ADMIN, roles.COO, roles.OPERATIONS, roles.FRONTDESK_MANAGER)

    @property
    def can_manage_production(self) -> bool:
        return self.has_role(roles.ADMIN, roles.COO, roles.MARKETING, roles.PRODUCTION_LEAD, roles.PRODUCTION_STAFF)

    @property
    def can_manage_marketing(self) -> bool:
        return self.has_role(roles.ADMIN, roles.COO, roles.MARKETING)

    @property
    def can_manage_compliance(self) -> bool:
        return self.has_role(roles.ADMIN, roles.COO)

    @property
    def can_manage_clients(self) -> bool:
        """Add or edit client records directly (not only through an enquiry)."""
        return self.can_edit_operations or self.has_role(roles.RECEPTIONIST)

    @property
    def can_run_movement(self) -> bool:
        """Check guides and vehicles out and in."""
        return self.has_role(*roles.MOVEMENT_OPERATORS)

    @property
    def can_manage_desk(self) -> bool:
        return self.has_role(roles.ADMIN, roles.FRONTDESK_MANAGER)

    @property
    def can_capture_enquiries(self) -> bool:
        return self.can_edit_operations or self.has_role(roles.SALES_AGENT, roles.MARKETING, roles.RECEPTIONIST)

    # ---- sales helpers
    @property
    def sees_all_sales(self) -> bool:
        return self.has_role(*roles.SALES_SEE_ALL)

    @property
    def can_manage_sales(self) -> bool:
        return self.has_role(*roles.SALES_MANAGERS)

    @property
    def sales_agent_profile(self):
        """The Employee record of a sales user (None when the user has no staff profile)."""
        return getattr(self, "employee", None)

    @property
    def can_work_sales(self) -> bool:
        """May create/change sales records (calls, quotations, bookings). Directors are read-only."""
        return self.has_role(roles.ADMIN, roles.COO, roles.SALES_MANAGER, roles.SALES_AGENT)
