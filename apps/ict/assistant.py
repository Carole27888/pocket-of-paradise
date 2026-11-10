"""In-app assistant: answers questions about the user's own data through read-only tools that reuse the same visibility rules as the screens."""
from django.db.models import Q

from . import ai

SYSTEM = """You are the in-app assistant for the Pocket of Paradise CRM. Help staff find information using the tools. Be brief and factual.
Only use tool results; if nothing is found, say so. Never guess figures. You cannot change data. Refer to records by their reference (e.g. ENQ-00012)."""

TOOLS = [
    {"name": "search_enquiries", "description": "Find enquiries/leads the user can see, by client name, phone or reference.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "search_clients", "description": "Find clients the user can see by name, phone or email.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "my_followups", "description": "The user's open follow-ups, soonest first.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "search_bookings", "description": "Find bookings the user can see by client name or booking reference. Gives paid and balance amounts.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "search_itineraries", "description": "Find itineraries the user can see by title, client or lead reference.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "company_knowledge", "description": "Look up the company's own approved answers (policies, payment terms, what packages include) taught in Bot Training.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "search_packages", "description": "Search published tour packages by title or code.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
]


def run_tool(user, name: str, args: dict):
    q = (args.get("query") or "").strip()[:80]
    if name == "search_enquiries":
        from apps.leads.views import visible_enquiries

        qs = visible_enquiries(user).filter(Q(client__full_name__icontains=q) | Q(client__phone__icontains=q) | Q(reference__icontains=q)).select_related("client")[:8]
        return [{"ref": e.reference, "client": e.client.full_name, "stage": e.get_stage_display(), "agent": e.assigned_agent.full_name if e.assigned_agent_id else None} for e in qs]
    if name == "search_clients":
        from apps.contacts.views import visible_clients

        return [{"name": c.full_name, "phone": c.phone, "email": c.email} for c in visible_clients(user).filter(Q(full_name__icontains=q) | Q(phone__icontains=q) | Q(email__icontains=q))[:8]]
    if name == "my_followups":
        from apps.sales.models import FollowUp

        emp = getattr(user, "employee", None)
        qs = FollowUp.objects.filter(done_at__isnull=True)
        qs = qs.filter(agent=emp) if emp else qs.none()
        return [{"title": f.title, "due": f.due_at.strftime("%d %b %H:%M"), "enquiry": f.enquiry.reference} for f in qs.select_related("enquiry")[:10]]
    if name == "search_bookings":
        from apps.sales.services import bookings_for

        qs = bookings_for(user).filter(Q(client__full_name__icontains=q) | Q(reference__icontains=q)).select_related("client")[:8]
        return [{"ref": b.reference, "client": b.client.full_name, "trip": b.title, "status": b.get_status_display(), "total": f"{b.currency} {b.total_amount:,.0f}",
                 "paid": f"{b.currency} {b.amount_paid:,.0f}", "balance": f"{b.currency} {b.balance:,.0f}"} for b in qs]
    if name == "search_itineraries":
        from apps.sales.services import itineraries_for

        qs = itineraries_for(user).filter(Q(title__icontains=q) | Q(enquiry__client__full_name__icontains=q) | Q(enquiry__reference__icontains=q))[:8]
        return [{"title": i.title, "lead": i.enquiry.reference, "days": i.day_count, "status": i.get_status_display(), "price": f"{i.currency} {i.sell_total:,.0f}", "client_views": i.share_views} for i in qs]
    if name == "company_knowledge":
        from . import knowledge

        return [{"question": e.question, "answer": e.answer} for _, e in knowledge.search(q, 4, 0.15)] or {"result": "Nothing taught about that yet."}
    if name == "search_packages":
        from apps.catalog.models import TourPackage

        return [{"code": p.code, "title": p.title, "days": p.days, "from": f"{p.currency} {p.price_per_person:,.0f}"}
                for p in TourPackage.objects.alive().filter(status="published").filter(Q(title__icontains=q) | Q(code__icontains=q))[:8]]
    return {"error": "unknown tool"}


def ask(user, question: str, history: list | None = None) -> str:
    msgs = [m for m in (history or []) if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)][-8:]
    msgs.append({"role": "user", "content": question[:800]})
    for _ in range(4):
        resp = ai.complete(SYSTEM + f"\nThe user is {user.full_name}.", msgs, tools=TOOLS, max_tokens=500, feature="assistant")
        if resp.get("stop_reason") != "tool_use":
            return ai.text_of(resp) or "I couldn't find an answer."
        msgs.append({"role": "assistant", "content": resp["content"]})
        results = []
        for b in resp["content"]:
            if b.get("type") == "tool_use":
                import json

                results.append({"type": "tool_result", "tool_use_id": b["id"], "content": json.dumps(run_tool(user, b["name"], b.get("input") or {}))})
        msgs.append({"role": "user", "content": results})
    return "That took too many steps. Try a more specific question."
