import django_filters as df

from apps.people.models import Employee

from .models import Client


class ClientFilter(df.FilterSet):
    is_international = df.ChoiceFilter(label="Market", choices=[("true", "International / diaspora"),
                                                                 ("false", "Domestic")],
                                       method="filter_market", empty_label="All markets")

    owner = df.ModelChoiceFilter(queryset=lambda r: Employee.objects.filter(is_sales_agent=True),
                                 empty_label="All owners")

    class Meta:
        model = Client
        fields = ["is_international", "owner"]

    def filter_market(self, qs, name, value):
        return qs.filter(is_international=(value == "true"))
