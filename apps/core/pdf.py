"""PDF rendering with WeasyPrint. When the engine (or its system libraries) is missing, fall back to the print page."""
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string


def render_pdf(request, template: str, ctx: dict, filename: str, fallback_url: str = "/"):
    try:
        from weasyprint import HTML
    except Exception:  # pragma: no cover - depends on the host
        messages.info(request, "PDF engine not installed here; use Print → Save as PDF.")
        return redirect(fallback_url)
    html = render_to_string(template, ctx, request)
    pdf = HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf()
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{filename}"'
    return resp
