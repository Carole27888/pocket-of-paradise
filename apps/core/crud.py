"""Create/update screens shared by catalogue, fleet, clients and staff."""
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.views.generic import CreateView, UpdateView

from apps.accounts.permissions import ModuleMixin


class FormScreenMixin(ModuleMixin):
    edit_required = True
    template_name = "components/form_page.html"
    title = ""
    cancel_url_name = ""
    success_message = "Saved."

    @property
    def in_modal(self):
        """The pop-up loads just the form (fetch with X-Requested-With) and re-submits it the same way."""
        return self.request.headers.get("X-Requested-With") == "XMLHttpRequest" and not self.request.htmx

    def get_template_names(self):
        return ["components/modal_form.html"] if self.in_modal else super().get_template_names()

    def form_invalid(self, form):
        resp = super().form_invalid(form)
        if self.in_modal:
            resp.status_code = 422
        return resp

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = self.title or (f"Edit {self.object}" if getattr(self, "object", None) else "New")
        ctx["cancel_url"] = self.get_cancel_url()
        return ctx

    def get_cancel_url(self):
        obj = getattr(self, "object", None)
        if obj is not None and hasattr(obj, "get_absolute_url"):
            return obj.get_absolute_url()
        from django.urls import reverse

        return reverse(self.cancel_url_name) if self.cancel_url_name else "/"

    def form_valid(self, form):
        self.object = form.save()
        messages.success(self.request, self.success_message)
        url = self.get_success_url()
        if self.in_modal:
            return JsonResponse({"ok": True, "redirect": url})
        if self.request.htmx:
            resp = HttpResponse(status=204)
            resp["HX-Redirect"] = url
            return resp
        return redirect(url)

    def get_success_url(self):
        return self.object.get_absolute_url()


class CreateScreen(FormScreenMixin, CreateView):
    pass


class UpdateScreen(FormScreenMixin, UpdateView):
    pass
