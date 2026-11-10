import django_filters as df

from .models import Department, Employee


class EmployeeFilter(df.FilterSet):
    role = df.ChoiceFilter(label="Role", choices=[("agent", "Sales agents"), ("guide", "Guides"), ("driver", "Drivers")],
                           method="filter_role", empty_label="All staff")
    on_duty = df.ChoiceFilter(choices=Employee.DUTY_CHOICES, empty_label="Any status")
    department = df.ModelChoiceFilter(queryset=Department.objects.all(), empty_label="All departments")

    class Meta:
        model = Employee
        fields = ["on_duty", "department"]

    def filter_role(self, qs, name, value):
        return qs.filter(**{{"agent": "is_sales_agent", "guide": "is_guide", "driver": "is_driver"}[value]: True})
