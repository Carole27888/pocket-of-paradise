"""Builds Days_Safaris_System_Proposal.pdf (WeasyPrint) in the app's brand theme."""
import math
from pathlib import Path
from weasyprint import HTML

M, O, SUN, CREAM, INK, MUTE = "#1F6F6B", "#D98A22", "#EEB63F", "#FBF5EA", "#1F2A26", "#6B756F"
ROOT = Path(__file__).parent


def bars(labels, rev, tgt, w=900, h=260):
    top = max(max(rev), max(tgt)) * 1.1
    pad_l, pad_b, n = 8, 24, len(labels)
    cw = (w - pad_l) / n
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%">']
    for i in range(1, 4):
        y = (h - pad_b) * (1 - i / 3.3)
        out.append(f'<line x1="0" x2="{w}" y1="{y:.0f}" y2="{y:.0f}" stroke="#EADFCE" stroke-width="1"/>')
    pts = []
    for i, (l, r, t) in enumerate(zip(labels, rev, tgt)):
        x = pad_l + i * cw + cw * 0.22
        bh = (h - pad_b - 8) * r / top
        out.append(f'<rect x="{x:.0f}" y="{h - pad_b - bh:.0f}" width="{cw * 0.56:.0f}" height="{bh:.0f}" rx="5" fill="{M}"/>')
        out.append(f'<text x="{x + cw * 0.28:.0f}" y="{h - 8}" text-anchor="middle" font-size="11" fill="{MUTE}">{l}</text>')
        pts.append((x + cw * 0.28, h - pad_b - 8 - (h - pad_b - 8) * t / top))
    path = " ".join(f"{x:.0f},{y:.0f}" for x, y in pts)
    out.append(f'<polyline points="{path}" fill="none" stroke="{O}" stroke-width="3" stroke-linejoin="round"/>')
    out += [f'<circle cx="{x:.0f}" cy="{y:.0f}" r="4" fill="#fff" stroke="{O}" stroke-width="2.5"/>' for x, y in pts]
    return "".join(out) + "</svg>"


def funnel(rows, w=520):
    out, y = [f'<svg viewBox="0 0 {w} {len(rows) * 40}" width="100%">'], 0
    shades = [M, "#2A8580", "#9A5A0C", O, SUN]
    for i, (label, n, pct) in enumerate(rows):
        bw = max(8, (w - 200) * pct / 100)
        out.append(f'<text x="0" y="{y + 22}" font-size="15" font-weight="700" fill="{INK}">{label}</text>')
        out.append(f'<rect x="108" y="{y + 6}" width="{bw:.0f}" height="22" rx="5" fill="{shades[i]}"/>')
        out.append(f'<text x="{108 + bw + 8:.0f}" y="{y + 22}" font-size="15" font-weight="800" fill="{INK}">{n} <tspan fill="{MUTE}" font-weight="500">· {pct}%</tspan></text>')
        y += 40
    return "".join(out) + "</svg>"


def donut(items, size=150):
    total = sum(v for _, v, _ in items)
    cx = cy = size / 2
    r, sw = size / 2 - 14, 26
    out, ang = [f'<svg viewBox="0 0 {size} {size}" width="{size}">'], -math.pi / 2
    for _, v, c in items:
        a2 = ang + 2 * math.pi * v / total
        x1, y1, x2, y2 = cx + r * math.cos(ang), cy + r * math.sin(ang), cx + r * math.cos(a2), cy + r * math.sin(a2)
        out.append(f'<path d="M{x1:.1f},{y1:.1f} A{r},{r} 0 {1 if a2 - ang > math.pi else 0} 1 {x2:.1f},{y2:.1f}" fill="none" stroke="{c}" stroke-width="{sw}"/>')
        ang = a2
    out.append(f'<text x="{cx}" y="{cy + 2}" text-anchor="middle" font-size="26" font-weight="800" fill="{M}">{total}</text>'
               f'<text x="{cx}" y="{cy + 20}" text-anchor="middle" font-size="10.5" fill="{MUTE}">leads sampled</text></svg>')
    return "".join(out)


src = [("Facebook", 50, M), ("Instagram", 46, "#2A8580"), ("Call", 30, "#9A5A0C"), ("WhatsApp", 29, O),
       ("YouTube", 25, SUN), ("Other", 97, "#D9CBB5")]
stages = [("Enquiry", 86, "#9AA6A0"), ("Quoted", 64, "#3B6FD4"), ("Hot", 38, O), ("Itinerary", 29, "#7B4FD0"),
          ("Deposit", 14, "#D9A300"), ("Confirmed", 9, "#14B8A6")]


def stage_bars():
    mx = max(v for _, v, _ in stages)
    return "".join(
        f'<div class="sb"><span>{n}</span><div><i style="width:{100 * v / mx:.0f}%;background:{c}"></i></div><b>{v}</b></div>'
        for n, v, c in stages)


fun = funnel([("Enquiries", 277, 100), ("Contacted", 222, 80), ("Quoted", 191, 69), ("Booked", 39, 14), ("Confirmed", 25, 9)])
rev = bars(["Apr", "May", "Jun", "Jul", "Aug", "Sep"], [0.2, 0.13, 0.45, 0.16, 7.96, 17.68], [0, 9.8, 11, 9.1, 11.6, 9.7])
legend = "".join(f'<li><i style="background:{c};flex:none"></i><span>{n}</span> <b>{v}</b></li>' for n, v, c in src)

roadmap = [
    ("clock", "Clock-in / clock-out", "Staff can only clock in from inside the office (office network and location check). Hours feed monthly performance."),
    ("chart", "Monthly employee performance", "Calls, quotes, bookings, revenue and attendance per person, with a ranked scorecard at month end."),
    ("report", "End-of-day (EOD) reports", "Every employee files a short EOD report on their phone. You read them all in one place, wherever you are."),
    ("pkg", "Instant package builder", "Enter hotel prices and number of days: a polished, priced package and itinerary is generated in seconds. No more manual pricing errors."),
    ("mail", "Automatic emails & SMS", "Bulk emails to collected leads, plus automatic SMS and email reminders for follow-ups, payments and travel dates."),
    ("wa", "WhatsApp chatbot", "Replies to new WhatsApp leads instantly, captures their details and assigns an agent straight away."),
    ("web", "Website & expo lead capture", "Connected to the Pocket of Paradise website. Leads from the site and from expos flow straight into the pipeline."),
    ("db", "One central database", "Every lead, booking, payment and employee action in one place that you fully own and control."),
]
ICON = {"clock": "M12 7v5l3 2M12 3a9 9 0 100 18 9 9 0 000-18z", "chart": "M4 20V10M10 20V4M16 20v-7M21 20H3",
        "report": "M6 3h8l4 4v14H6zM14 3v4h4M9 12h6M9 16h6", "pkg": "M3 8l9-5 9 5v8l-9 5-9-5zM3 8l9 5 9-5M12 13v8",
        "mail": "M3 6h18v12H3zM3 7l9 7 9-7", "wa": "M4 20l1.4-4.2A8 8 0 1112 20a8 8 0 01-3.8-1zM9 9c0 3 3 6 6 6l1-2-2-1-1 .8c-1-.5-2-1.5-2.4-2.5L11 9.5l-1-2z",
        "web": "M12 3a9 9 0 100 18 9 9 0 000-18zM3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18", "db": "M5 6c0-1.7 3.1-3 7-3s7 1.3 7 3-3.1 3-7 3-7-1.3-7-3zM5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3"}


def ico(k, c="#fff"):
    return f'<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="{ICON[k]}"/></svg>'


rm = "".join(f'<div class="rm"><span class="ri">{ico(k)}</span><div><h4>{t}</h4><p>{d}</p></div></div>' for k, t, d in roadmap)

html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><style>
@font-face{{font-family:Manrope;font-weight:400;src:url(assets/manrope-latin-400-normal.woff2)}}
@font-face{{font-family:Manrope;font-weight:600;src:url(assets/manrope-latin-600-normal.woff2)}}
@font-face{{font-family:Manrope;font-weight:700;src:url(assets/manrope-latin-700-normal.woff2)}}
@font-face{{font-family:Manrope;font-weight:800;src:url(assets/manrope-latin-800-normal.woff2)}}
@font-face{{font-family:Fraunces;font-weight:700;src:url(assets/fraunces-latin-700-normal.woff2)}}
@font-face{{font-family:Fraunces;font-weight:800;src:url(assets/fraunces-latin-800-normal.woff2)}}
@font-face{{font-family:Fraunces;font-weight:500;font-style:italic;src:url(assets/fraunces-latin-500-italic.woff2)}}
@page{{size:A4;margin:0}}
*{{box-sizing:border-box}} body{{margin:0;font-family:Manrope,sans-serif;color:{INK};font-size:10.5pt;line-height:1.5}}
h1,h2,h3{{font-family:Fraunces,serif;color:{M};margin:0}} p{{margin:0}}
.page{{width:210mm;height:297mm;position:relative;overflow:hidden;page-break-after:always;background:{CREAM};padding:16mm 15mm 18mm}}
.page:last-child{{page-break-after:auto}}
.foot{{position:absolute;left:15mm;right:15mm;bottom:8mm;display:flex;justify-content:space-between;font-size:8pt;color:{MUTE}}}
.eyebrow{{font-size:8.5pt;letter-spacing:.14em;text-transform:uppercase;font-weight:800;color:{O}}}
h2{{font-size:22pt;line-height:1.1;margin:3px 0 6px}} .lead{{color:{MUTE};font-size:10.5pt;max-width:150mm}}
/* cover */
.cover{{background:{M};color:#fff;padding:0}}
.cover .sun{{position:absolute;right:-60mm;top:-50mm;width:190mm;height:190mm;border-radius:50%;opacity:.95}}
.cover .in{{position:absolute;left:18mm;right:18mm;bottom:26mm}}
.cover img.logo{{position:absolute;left:18mm;top:16mm;width:58mm;background:{CREAM};border-radius:10px;padding:5mm}}
.cover h1{{color:#fff;font-size:40pt;line-height:1.04}} .cover h1 em{{color:{SUN};font-style:italic;font-weight:500}}
.cover p{{color:#F3D9BF;font-size:12.5pt;margin-top:7mm;max-width:130mm}}
.cover .meta{{margin-top:12mm;display:flex;gap:10mm;font-size:9pt;color:#EBC7A4;border-top:1px solid #2A8580;padding-top:5mm}} .cover .meta b{{display:block;color:#fff;font-size:10.5pt}}
/* cards */
.card{{background:#fff;border:1px solid #EADFCE;border-radius:12px;padding:5mm}}
.grid{{display:grid;gap:5mm}} .g2{{grid-template-columns:1fr 1fr}} .g3{{grid-template-columns:1fr 1fr 1fr}} .g4{{grid-template-columns:repeat(4,1fr)}}
.kpi b{{display:block;font-family:Fraunces,serif;font-size:19pt;color:{M};line-height:1.1}} .kpi span{{font-size:8.5pt;color:{MUTE}}}
.kpi.a{{border-top:3px solid {O}}}
.pain{{display:flex;gap:3mm;align-items:flex-start;margin-bottom:3.2mm}} .pain i{{flex:none;width:6mm;height:6mm;border-radius:50%;background:#E2F1EE;color:#B4231A;display:grid;place-items:center;font-style:normal;font-weight:800;font-size:9pt}}
.pain.ok i{{background:#DDF3E7;color:#1F7A4D}} .pain p{{font-size:9.6pt}} h3{{font-size:13pt;margin-bottom:3mm}}
.shot{{border-radius:10px;border:1px solid #D9CBB5;box-shadow:0 6px 18px rgba(31,111,107,.18);width:100%;display:block}}
.cap{{font-size:8.3pt;color:{MUTE};margin-top:2mm}} .tag{{display:inline-block;background:#DDF3E7;color:#1F7A4D;font-weight:800;font-size:7.8pt;padding:1px 8px;border-radius:99px;letter-spacing:.04em}}
.tag.n{{background:#FFEBD0;color:#9A5A0C}}
.sb{{display:grid;grid-template-columns:62px 1fr 26px;gap:6px;align-items:center;font-size:8.8pt;margin-bottom:5px}} .sb div{{background:#F0E8DC;border-radius:99px;height:9px}} .sb i{{display:block;height:9px;border-radius:99px}} .sb b{{text-align:right}}
ul.lg{{list-style:none;padding:0;margin:0;font-size:8.8pt}} ul.lg li{{margin:3px 0;display:flex;align-items:center;gap:6px}} ul.lg i{{display:inline-block;width:9px;height:9px;border-radius:3px}} ul.lg b{{margin-left:auto}}
.mob{{width:42mm;border-radius:18px;border:3px solid {M};box-shadow:0 6px 18px rgba(31,111,107,.25);display:block}}
.rm{{display:flex;gap:4mm;background:#fff;border:1px solid #EADFCE;border-radius:12px;padding:4mm;margin-bottom:3.4mm}}
.ri{{flex:none;width:11mm;height:11mm;border-radius:10px;background:{M};display:grid;place-items:center}} .rm h4{{margin:0 0 1px;font-size:10.5pt;color:{M}}} .rm p{{font-size:8.9pt;color:#44524C;line-height:1.4}}
.band{{background:{M};color:#fff;border-radius:14px;padding:7mm 8mm}} .band h2{{color:#fff}} .band p{{color:#F3D9BF}}
.step{{display:flex;gap:4mm;margin-bottom:3.5mm;align-items:flex-start}} .step b.n{{flex:none;width:8mm;height:8mm;border-radius:50%;background:{O};color:#fff;display:grid;place-items:center;font-size:10pt}}
.step h4{{margin:0;font-size:10.5pt;color:{M}}} .step p{{font-size:9pt;color:#44524C}}
.price{{background:#fff;border:2px dashed {O};border-radius:14px;padding:6mm 8mm;text-align:center}} .price b{{font-family:Fraunces,serif;font-size:19pt;color:{M};display:block}}
.demo{{font-size:7.6pt;color:{MUTE};font-style:italic}}
</style></head><body>

<section class="page cover"><svg class="sun" viewBox="0 0 200 200"><circle cx="100" cy="100" r="100" fill="#FFD9A8"/><circle cx="100" cy="100" r="82" fill="#EDA036"/><circle cx="100" cy="100" r="62" fill="#D98A22"/><circle cx="100" cy="100" r="40" fill="#EEB63F"/></svg><img class="logo" src="assets/logo.png">
 <div class="in"><div class="eyebrow" style="color:{SUN}">Proposal for management</div>
  <h1>One system.<br>Every lead, shilling<br>and employee <em>in view.</em></h1>
  <p>A custom operating system for Pocket of Paradise Travel Company: sales CRM, staff accountability, automation and reporting, in your hands, on your phone, under your full control.</p>
  <div class="meta"><div>Prepared for<b>Management, Pocket of Paradise Travel Company</b></div><div>Date<b>5 October 2026</b></div><div>Status<b>Sales CRM already built &amp; running</b></div></div></div></section>

<section class="page"><div class="eyebrow">Why this matters</div><h2>From scattered leads to one source of truth</h2>
 <p class="lead">Today leads, prices and staff activity live in chats, notebooks and memory. That makes accountability hard and revenue invisible. This system fixes it at the root.</p>
 <div class="grid g2" style="margin-top:7mm">
  <div class="card"><h3>What hurts today</h3>
   <div class="pain"><i>✕</i><p>Leads from WhatsApp, calls, expos and the website are scattered and some are never followed up.</p></div>
   <div class="pain"><i>✕</i><p>Packages are priced by hand: slow and error-prone, as happened with the Kuosha package.</p></div>
   <div class="pain"><i>✕</i><p>No clear view of who is working, who is performing, or what each person contributes.</p></div>
   <div class="pain"><i>✕</i><p>Reminders and follow-up emails depend on someone remembering.</p></div>
   <div class="pain"><i>✕</i><p>Paying monthly for third-party tools, without owning the data.</p></div></div>
  <div class="card"><h3>What you get</h3>
   <div class="pain ok"><i>✓</i><p>Every lead captured once, assigned instantly and tracked to booking.</p></div>
   <div class="pain ok"><i>✓</i><p>Packages generated in seconds from hotel prices and number of days.</p></div>
   <div class="pain ok"><i>✓</i><p>Clock-in, EOD reports and a monthly scorecard for every employee.</p></div>
   <div class="pain ok"><i>✓</i><p>Automatic SMS and email reminders and bulk campaigns to your leads.</p></div>
   <div class="pain ok"><i>✓</i><p>Full ownership: one central database, no dependence on outside platforms.</p></div></div></div>
 <div class="grid g4" style="margin-top:6mm">
  <div class="card kpi a" style="padding:4mm"><b>1</b><span>central database</span></div>
  <div class="card kpi"><b>8</b><span>modules planned</span></div>
  <div class="card kpi"><b>24/7</b><span>visibility from any phone</span></div>
  <div class="card kpi"><b>0</b><span>outside CRM fees</span></div></div>
 <div class="card" style="margin-top:6mm"><h3>It installs on any phone</h3><p style="font-size:9.6pt;color:#44524C">The system is a web app that installs like a normal app on Android and iPhone. Management can open it anywhere to see leads, bookings and the team's activity as it happens. Staff use it from the office or the field.</p></div>
<div class="card" style="margin-top:6mm"><h3>The journey of a lead, automated</h3>
 <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:3mm;text-align:center">
 {"".join(f'<div><div style="width:12mm;height:12mm;margin:0 auto 2mm;border-radius:50%;background:{c};color:#fff;display:grid;place-items:center;font-weight:800;font-size:12pt">{i}</div><b style="font-size:9.5pt;color:{M}">{t}</b><div style="font-size:8.3pt;color:{MUTE}">{d}</div></div>' for i,(t,d,c) in enumerate([("Captured","Website, expo, WhatsApp, call",M),("Assigned","Instantly, by rules",'#2A8580'),("Followed up","Reminders by SMS & email",'#9A5A0C'),("Quoted","Package built in seconds",O),("Booked","Deposit tracked, revenue seen",'#14B8A6')],1))}
 </div></div>
 <div class="foot"><span>Pocket of Paradise Travel Company · System proposal</span><span>2</span></div></section>

<section class="page"><div class="eyebrow">Already built <span class="tag">LIVE NOW</span></div><h2>Sales CRM &amp; pipeline: working today</h2>
 <p class="lead">The sales module is finished and running with demo data. Managers see everything; each agent sees only their own work.</p>
 <img class="shot" style="margin-top:6mm" src="assets/dashboard.png"><div class="cap">Sales Manager dashboard: pipeline value, revenue won, conversion, agent leaderboard and live team activity. <span class="demo">(Demo data)</span></div>
 <div class="grid g2" style="margin-top:6mm">
  <div class="card"><h3>Lead-to-booking funnel</h3>{fun}<div class="demo">Sample of 277 demo enquiries</div></div>
  <div class="card"><h3>Where leads come from</h3><div style="display:flex;align-items:center;gap:4mm"><div style="flex:none;width:36mm">{donut(src)}</div><ul class="lg" style="flex:1">{legend}</ul></div></div></div>
 <div class="foot"><span>Pocket of Paradise Travel Company · System proposal</span><span>3</span></div></section>

<section class="page"><div class="eyebrow">Management control <span class="tag">LIVE NOW</span></div><h2>See every agent. Move every deal.</h2>
 <p class="lead">Drag deals across the pipeline, assign leads by rules or round-robin, send versioned quotations, record deposits, and track each agent against monthly targets.</p>
 <div class="grid" style="grid-template-columns:1.55fr 1fr;margin-top:6mm;align-items:start">
  <div><img class="shot" src="assets/pipeline.png"><div class="cap">Pipeline board: drag a lead from Enquiry to Quoted to Hot to Itinerary. <span class="demo">(Demo data)</span></div>
   <img class="shot" style="margin-top:5mm" src="assets/team.png"><div class="cap">Team view: revenue, open leads, conversion, calls, overdue tasks and target progress per agent.</div></div>
  <div style="display:grid;gap:5mm"><div style="display:flex;gap:4mm;justify-content:center"><img class="mob" src="assets/mobile.png"><img class="mob" src="assets/mobile2.png"></div>
   <div class="cap" style="text-align:center;margin-top:-2mm">On a phone: the same system, built for the field.</div>
   <div class="card"><h3>Pipeline by stage</h3>{stage_bars()}</div></div></div>
 <div class="card" style="margin-top:5mm"><h3>Revenue vs target (KES millions)</h3>{rev}<div class="demo">Bars: revenue won. Line: monthly target. Demo data.</div></div>
 <div class="foot"><span>Pocket of Paradise Travel Company · System proposal</span><span>4</span></div></section>

<section class="page"><div class="eyebrow">What we build next <span class="tag n">PROPOSED</span></div><h2>Built around how your office actually works</h2>
 <p class="lead">Based on today's meeting, these modules extend the CRM into a full operating system connected to the Pocket of Paradise website.</p>
 <div class="grid g2" style="margin-top:6mm"><div>{"".join(f'<div class="rm"><span class="ri">{ico(k)}</span><div><h4>{t}</h4><p>{d}</p></div></div>' for k, t, d in roadmap[:4])}</div>
 <div>{"".join(f'<div class="rm"><span class="ri">{ico(k)}</span><div><h4>{t}</h4><p>{d}</p></div></div>' for k, t, d in roadmap[4:])}</div></div>
 <div class="band" style="margin-top:3mm"><div class="eyebrow" style="color:{SUN}">The result</div><h2>Accountability you can see</h2>
  <p>Know how much the company is making, how much each employee is contributing, and which channels and packages work best, from one dashboard, any time.</p></div>
 <div class="foot"><span>Pocket of Paradise Travel Company · System proposal</span><span>5</span></div></section>

<section class="page"><div class="eyebrow">Next steps</div><h2>Ready to start immediately</h2>
 <p class="lead">The foundation is already built, so new modules can be added without starting from scratch.</p>
 <div class="grid g2" style="margin-top:7mm;align-items:start"><div>
  <div class="step"><b class="n">1</b><div><h4>Live walkthrough</h4><p>See the working Sales CRM with your team and confirm priorities.</p></div></div>
  <div class="step"><b class="n">2</b><div><h4>Connect the website &amp; WhatsApp</h4><p>Leads from the site, expos and WhatsApp start flowing into the pipeline, with the chatbot assigning agents instantly.</p></div></div>
  <div class="step"><b class="n">3</b><div><h4>Staff accountability</h4><p>Clock-in, EOD reports and monthly performance scorecards go live for the team.</p></div></div>
  <div class="step"><b class="n">4</b><div><h4>Automation &amp; packages</h4><p>Email/SMS reminders, bulk campaigns and the instant package builder.</p></div></div>
  <div class="step"><b class="n">5</b><div><h4>Training &amp; handover</h4><p>Staff install the app on their phones; you get full control of the data.</p></div></div></div>
 <div><img class="shot" src="assets/quote.png"><div class="cap">A quotation generated by the system: priced lines, VAT, versions, PDF and WhatsApp-ready.</div></div></div>
 <div class="price" style="margin-top:8mm"><div class="eyebrow">Investment</div><b>Pricing is available upon request</b><p style="font-size:9.6pt;color:#44524C;margin-top:2mm">We will tailor a quotation to the modules you want first. Let's talk this week.</p></div>
 <div class="foot"><span>Pocket of Paradise Travel Company · Travel Company</span><span>6</span></div></section>
</body></html>"""
(ROOT / "proposal.html").write_text(html)
HTML(string=html, base_url=str(ROOT)).write_pdf(ROOT / "Days_Safaris_System_Proposal.pdf")
print("ok")
