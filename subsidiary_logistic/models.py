from django.db import models
from logistics_performance.models import AuditSoftDeleteModel


class SubLogisticEntry(AuditSoftDeleteModel):
    """One row = one country's logistics cost figure for a given period date."""

    country            = models.CharField(max_length=120)
    total_sales        = models.DecimalField(max_digits=12, decimal_places=2)
    ob_logistics_cost  = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    operational_cost   = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    period_date        = models.DateField()

    class Meta:
        db_table   = "sub_logistic_entry"
        ordering   = ["-period_date", "country"]
        verbose_name        = "Sub Logistic Entry"
        verbose_name_plural = "Sub Logistic Entries"

    def __str__(self):
        return f"{self.country} · {self.period_date}"
