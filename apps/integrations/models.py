from django.db import models


class IntegrationLog(models.Model):
    """Every outbound integration call (mock or live) is recorded for troubleshooting."""

    service = models.CharField(max_length=40, db_index=True)
    action = models.CharField(max_length=60)
    mode = models.CharField(max_length=6, default="mock")
    ok = models.BooleanField(default=True)
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.service}.{self.action} ({self.mode})"
