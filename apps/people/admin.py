from django.contrib import admin

from .models import Department, Employee, Specialty


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("full_name", "title", "department", "is_sales_agent", "is_guide", "is_driver", "on_duty", "is_active")
    list_filter = ("department", "is_sales_agent", "is_guide", "is_driver", "on_duty", "is_active")
    search_fields = ("full_name", "phone", "email")
    filter_horizontal = ("specialties",)


admin.site.register(Department)
admin.site.register(Specialty)
