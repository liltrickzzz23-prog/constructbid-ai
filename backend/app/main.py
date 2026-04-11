"""
ConstructBid AI — Government Contractor OS v4
FastAPI backend with PostgreSQL, SAM.gov, auto-refresh, notifications, voice AI.
"""

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import json, os, uuid, httpx, asyncio, re, base64
from datetime import datetime, date, timedelta
from contextlib import asynccontextmanager

from app.database import init_db, get_db, SessionLocal, Company, Opportunity, Project

# ── Helpers to convert DB rows to dicts ──
def company_to_dict(c):
    return {
        "id": c.id, "name": c.name, "services": c.services or [],
        "certifications": c.certifications or [], "naics": c.naics or [],
        "bonding_capacity": c.bonding_capacity or 0, "regions": c.regions or [],
        "sam_api_key": c.sam_api_key or "",
        "notify_email": c.notify_email or "", "notify_phone": c.notify_phone or "",
        "notify_enabled": c.notify_enabled or False, "notify_min_score": c.notify_min_score or 75,
    }

def opp_to_dict(o):
    return {
        "id": o.id, "company_id": o.company_id, "title": o.title or "",
        "agency": o.agency or "", "naics": o.naics or "", "location": o.location or "",
        "due_date": o.due_date or "", "value": o.value or 0, "set_aside": o.set_aside or "",
        "scope": o.scope or "", "status": o.status or "new", "source": o.source or "manual",
        "sam_notice_id": o.sam_notice_id, "sam_sol_number": o.sam_sol_number or "",
        "sam_posted_date": o.sam_posted_date or "", "sam_type": o.sam_type or "",
        "sam_link": o.sam_link or "",
    }

def proj_to_dict(p):
    return {"id": p.id, "company_id": p.company_id, "name": p.name or "",
            "client": p.client or "", "value": p.value or 0, "year": p.year or 0, "scope": p.scope or ""}

# ── Load env from file ──
def load_env_var(name):
    val = os.environ.get(name, "")
    if val:
        return val
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.strip().startswith(f"{name}="):
                    return line.strip().split("=", 1)[1].strip().strip('"').strip("'")
    return ""


# ── Notification System ──
async def send_notifications(company: dict, new_pursue_opps: list):
    if not company.get("notify_enabled") or not new_pursue_opps:
        return
    email = company.get("notify_email", "")
    phone = company.get("notify_phone", "")
    comp_name = company.get("name", "Your Company")
    count = len(new_pursue_opps)
    subject = f"🔥 {count} New High-Score Opportunit{'ies' if count > 1 else 'y'} — ConstructBid AI"

    text_lines = [f"ConstructBid AI found {count} new opportunit{'ies' if count > 1 else 'y'} for {comp_name}:\n"]
    for opp in new_pursue_opps[:5]:
        text_lines.append(f"• [{opp.get('score',0)} pts] {opp.get('title','')}")
        text_lines.append(f"  Agency: {opp.get('agency','')}")
        val = opp.get('value', 0)
        text_lines.append(f"  Value: {'${:.1f}M'.format(val/1e6) if val else 'TBD'} | Due: {opp.get('due_date','TBD')}")
        if opp.get('sam_link'): text_lines.append(f"  {opp['sam_link']}")
        text_lines.append("")
    plain_text = "\n".join(text_lines)

    html_rows = ""
    for opp in new_pursue_opps[:5]:
        score = opp.get("score", 0)
        color = "#22c55e" if opp.get("recommendation") == "PURSUE" else "#f59e0b"
        val = opp.get("value", 0)
        val_str = f"${val/1e6:.1f}M" if val else "TBD"
        html_rows += f'<tr style="border-bottom:1px solid #1e2d3d"><td style="padding:12px;text-align:center"><span style="display:inline-block;width:44px;height:44px;border-radius:50%;border:3px solid {color};line-height:38px;text-align:center;font-weight:700;color:{color};font-size:14px">{score}</span></td><td style="padding:12px"><strong style="color:#e2e8f0;font-size:14px">{opp.get("title","")}</strong><br><span style="color:#64748b;font-size:12px">{opp.get("agency","")}</span><br><span style="color:#64748b;font-size:12px">{val_str} · Due {opp.get("due_date","TBD")}</span></td></tr>'

    html_body = f'<div style="background:#0a0f1a;padding:20px;font-family:Arial,sans-serif"><div style="max-width:600px;margin:0 auto;background:#111827;border-radius:12px;overflow:hidden;border:1px solid #1e2d3d"><div style="background:linear-gradient(135deg,#059669,#06b6d4);padding:20px;text-align:center"><h1 style="color:white;margin:0;font-size:20px">🔥 {count} New Opportunit{"ies" if count > 1 else "y"} Found</h1><p style="color:rgba(255,255,255,0.8);margin:8px 0 0;font-size:14px">ConstructBid AI — {comp_name}</p></div><table style="width:100%;border-collapse:collapse">{html_rows}</table><div style="padding:20px;text-align:center"><a href="https://web-production-4fc55.up.railway.app" style="display:inline-block;background:#3b82f6;color:white;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px">Open Dashboard →</a></div></div></div>'

    if email:
        resend_key = load_env_var("RESEND_API_KEY")
        if resend_key:
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post("https://api.resend.com/emails",
                        headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
                        json={"from": "ConstructBid AI <onboarding@resend.dev>", "to": [email], "subject": subject, "html": html_body, "text": plain_text})
                    print(f"[NOTIFY] Email {'sent' if resp.status_code in (200,201) else 'failed'} to {email}: {resp.status_code}")
            except Exception as e:
                print(f"[NOTIFY] Email error: {e}")

    if phone:
        twilio_sid = load_env_var("TWILIO_ACCOUNT_SID")
        twilio_token = load_env_var("TWILIO_AUTH_TOKEN")
        twilio_from = load_env_var("TWILIO_FROM_NUMBER")
        if twilio_sid and twilio_token and twilio_from:
            sms = f"ConstructBid AI: {count} new high-score opportunit{'ies' if count > 1 else 'y'}!\nTop: {new_pursue_opps[0].get('title','')[:60]} ({new_pursue_opps[0].get('score',0)} pts)"
            try:
                auth = base64.b64encode(f"{twilio_sid}:{twilio_token}".encode()).decode()
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json",
                        headers={"Authorization": f"Basic {auth}"}, data={"To": phone, "From": twilio_from, "Body": sms})
                    print(f"[NOTIFY] SMS {'sent' if resp.status_code in (200,201) else 'failed'} to {phone}: {resp.status_code}")
            except Exception as e:
                print(f"[NOTIFY] SMS error: {e}")


# ── Scoring Engine v2 ──
SCOPE_KEYWORDS = [
    "cemetery", "columbarium", "gravesite", "burial", "headstone", "niche", "memorial", "interment", "cremation", "mausoleum",
    "construction", "renovation", "remodel", "expansion", "addition", "demolition", "design-build", "design build", "build-out", "tenant improvement",
    "facilities", "maintenance", "janitorial", "custodial", "grounds", "landscaping", "mowing", "irrigation", "snow removal",
    "hvac", "plumbing", "mechanical", "electrical", "roofing", "painting", "flooring", "concrete", "masonry", "carpentry", "fire protection",
    "site prep", "site preparation", "excavation", "grading", "paving", "asphalt", "road repair", "drainage", "utilities", "fencing", "signage",
    "restoration", "historic", "historical", "rehabilitation", "remediation",
    "lease", "leasing", "outpatient clinic", "cboc", "medical center", "office space",
    "repair", "replace", "install", "upgrade", "improve", "modify",
]

def score_opportunity(opp: dict, company: dict) -> dict:
    score = 0
    reasons = []
    flags = []

    comp_naics = company.get("naics", [])
    comp_certs = [c.upper() for c in company.get("certifications", [])]
    comp_regions = company.get("regions", [])
    comp_services = [s.lower() for s in company.get("services", [])]
    comp_bonding = company.get("bonding_capacity", 0)

    opp_naics = opp.get("naics", "") or ""
    opp_set_aside = opp.get("set_aside", "") or ""
    opp_location = opp.get("location", "") or ""
    opp_value = opp.get("value", 0) or 0
    opp_due = opp.get("due_date", "") or ""
    opp_text = ((opp.get("title", "") or "") + " " + (opp.get("scope", "") or "")).lower()

    # 1. NAICS (35 pts)
    if opp_naics in comp_naics:
        score += 35; reasons.append(f"✓ NAICS {opp_naics} — exact match")
    elif opp_naics[:4] and any(n[:4] == opp_naics[:4] for n in comp_naics):
        score += 25; reasons.append(f"◐ NAICS {opp_naics} — closely related")
    elif opp_naics[:3] and any(n[:3] == opp_naics[:3] for n in comp_naics):
        score += 15; reasons.append(f"○ NAICS {opp_naics} — same industry group")
    elif opp_naics[:2] and any(n[:2] == opp_naics[:2] for n in comp_naics):
        score += 8; reasons.append(f"△ NAICS {opp_naics} — same sector, weak alignment")
    else:
        score += 0; reasons.append(f"✗ NAICS {opp_naics} — outside your capabilities")

    # 2. Set-Aside (25 pts)
    sa_upper = opp_set_aside.upper().strip()
    has_sdvosb = any("SDVOSB" in c for c in comp_certs)
    sa_eligible = False
    if not sa_upper or sa_upper in ("FULL & OPEN", "FULL AND OPEN", "NONE", "N/A"):
        score += 15; reasons.append("○ Full & open — more competition"); sa_eligible = True
    elif "SDVOSB" in sa_upper:
        if has_sdvosb: score += 25; reasons.append("✓ SDVOSB set-aside matches"); sa_eligible = True
        else: reasons.append("✗ SDVOSB required — not certified"); flags.append("set_aside_disqualified")
    elif "SMALL" in sa_upper or "SBA" in sa_upper:
        score += 20; reasons.append("✓ Small business set-aside"); sa_eligible = True
    elif "8(A)" in sa_upper or "8A" in sa_upper:
        if any("8(A)" in c or "8A" in c for c in comp_certs): score += 25; reasons.append("✓ 8(a) matches"); sa_eligible = True
        else: reasons.append("✗ 8(a) required — not certified"); flags.append("set_aside_disqualified")
    elif "HUBZONE" in sa_upper:
        if any("HUBZONE" in c for c in comp_certs): score += 25; reasons.append("✓ HUBZone matches"); sa_eligible = True
        else: reasons.append("✗ HUBZone required — not certified"); flags.append("set_aside_disqualified")
    elif "WOSB" in sa_upper or "EDWOSB" in sa_upper:
        if any("WOSB" in c or "EDWOSB" in c for c in comp_certs): score += 25; sa_eligible = True
        else: reasons.append("✗ WOSB required — not certified"); flags.append("set_aside_disqualified")
    elif "VOSB" in sa_upper:
        if has_sdvosb: score += 25; reasons.append("✓ VOSB — SDVOSB qualifies"); sa_eligible = True
        else: reasons.append("✗ VOSB required — not certified"); flags.append("set_aside_disqualified")
    else:
        score += 10; reasons.append(f"? Set-aside '{opp_set_aside}' — verify eligibility"); sa_eligible = True

    # 3. Keywords (20 pts)
    dynamic_kw = {w.lower() for s in comp_services for w in s.split() if len(w) > 3}
    all_kw = set(SCOPE_KEYWORDS) | dynamic_kw
    matched = [k for k in all_kw if k in opp_text]
    high_signal = ["cemetery", "columbarium", "gravesite", "burial", "memorial", "interment"]
    high = [k for k in high_signal if k in opp_text]
    if high: kw_score = min(20, 12 + len(matched) * 2); reasons.append(f"✓ Strong match: {', '.join(high[:3])}")
    elif len(matched) >= 4: kw_score = min(20, len(matched) * 3); reasons.append(f"✓ Good alignment: {', '.join(list(matched)[:5])}")
    elif len(matched) >= 2: kw_score = min(14, len(matched) * 4); reasons.append(f"◐ Partial match: {', '.join(list(matched)[:4])}")
    elif len(matched) == 1: kw_score = 5; reasons.append(f"○ Weak match: {matched[0]}")
    elif opp_naics in comp_naics: kw_score = 8; reasons.append("○ No scope details — NAICS suggests relevance")
    else: kw_score = 0; reasons.append("✗ No keywords match")
    score += kw_score

    # 4. Location (10 pts)
    adjacent = {"VA":["MD","DC","WV","NC","TN","KY"],"MD":["VA","DC","WV","PA","DE"],"DC":["VA","MD"],"OH":["MI","IN","KY","WV","PA"],"MI":["OH","IN","WI"],"IN":["MI","OH","IL","KY"],"OK":["TX","KS","AR","MO"],"NE":["KS","SD","IA","CO","WY"],"CA":["OR","NV","AZ"],"TN":["VA","NC","GA","AL","MS","AR","MO","KY"],"WY":["MT","SD","NE","CO","UT","ID"]}
    if not opp_location: score += 6; reasons.append("○ Location not specified")
    elif opp_location in comp_regions: score += 10; reasons.append(f"✓ Located in {opp_location}")
    elif any(opp_location in adjacent.get(r, []) for r in comp_regions): score += 5; reasons.append(f"◐ {opp_location} — adjacent to your region")
    else: score += 1; reasons.append(f"△ {opp_location} — outside your region")

    # 5. Bonding (5 pts)
    if opp_value <= 0: score += 4; reasons.append("○ Value not disclosed")
    elif opp_value <= comp_bonding * 0.5: score += 5; reasons.append(f"✓ ${opp_value/1e6:.1f}M — well within bonding")
    elif opp_value <= comp_bonding: score += 4; reasons.append(f"✓ ${opp_value/1e6:.1f}M — within bonding")
    elif opp_value <= comp_bonding * 1.5: score += 2; reasons.append(f"△ ${opp_value/1e6:.1f}M — near bonding limit")
    else: score += 0; reasons.append(f"✗ ${opp_value/1e6:.1f}M — exceeds bonding")

    # 6. Timeline (5 pts)
    try:
        if opp_due:
            days_left = (datetime.strptime(opp_due, "%Y-%m-%d").date() - date.today()).days
            if days_left < 0: flags.append("expired"); reasons.append("✗ Deadline passed")
            elif days_left > 30: score += 5; reasons.append(f"✓ {days_left} days — comfortable")
            elif days_left > 14: score += 3; reasons.append(f"◐ {days_left} days — tight")
            elif days_left > 3: score += 1; reasons.append(f"△ {days_left} days — rush")
            else: flags.append("deadline_critical"); reasons.append(f"✗ {days_left} day(s) left")
        else: score += 3; reasons.append("○ No deadline specified")
    except: score += 3; reasons.append("○ Could not parse deadline")

    score = min(100, score)
    if "set_aside_disqualified" in flags: score = min(score, 35); reasons.insert(0, "⚠ DISQUALIFIED — set-aside not met")
    if "expired" in flags: score = min(score, 25); reasons.insert(0, "⚠ EXPIRED — deadline passed")

    rec = "PASS" if ("set_aside_disqualified" in flags or "expired" in flags) else "PURSUE" if score >= 75 else "REVIEW" if score >= 55 else "PASS"
    return {"score": score, "recommendation": rec, "reasons": reasons}


# ── SAM.gov Integration ──
SAM_API_URL = "https://api.sam.gov/prod/opportunities/v2/search"
SET_ASIDE_MAP = {"SBA":"Small Business","SBP":"Small Business","8A":"8(a)","8AN":"8(a)","HZC":"HUBZone","HZS":"HUBZone","SDVOSBC":"SDVOSB","SDVOSBS":"SDVOSB","VOSBC":"VOSB","VOSBS":"VOSB","WOSB":"WOSB","WOSBSS":"WOSB","EDWOSB":"EDWOSB","":"Full & Open",None:"Full & Open"}

async def fetch_sam_opportunities(company: dict, days_back: int = 30) -> list:
    api_key = company.get("sam_api_key", "")
    if not api_key: return []

    posted_from = (date.today() - timedelta(days=days_back)).strftime("%m/%d/%Y")
    posted_to = date.today().strftime("%m/%d/%Y")
    naics_codes = company.get("naics", [])

    priority_prefixes = ["8122", "2362", "2369", "5617", "2382", "5612", "2379", "2389", "2361"]
    priority = [n for n in naics_codes if any(n.startswith(p) for p in priority_prefixes)]
    other = [n for n in naics_codes if n not in priority]
    search_codes = (priority + other)[:8]

    all_opps = []
    # Get existing notice IDs from database
    session = SessionLocal()
    try:
        existing_notice_ids = {o.sam_notice_id for o in session.query(Opportunity.sam_notice_id).filter(Opportunity.sam_notice_id.isnot(None)).all()}
    finally:
        session.close()

    calls_made = 0
    async with httpx.AsyncClient(timeout=30.0) as client:
        for naics in search_codes:
            try:
                params = {"api_key": api_key, "limit": 100, "offset": 0, "postedFrom": posted_from, "postedTo": posted_to, "ncode": naics, "ptype": "p,o,k"}
                if calls_made > 0: await asyncio.sleep(1.5)
                resp = await client.get(SAM_API_URL, params=params)
                calls_made += 1
                print(f"SAM.gov call #{calls_made}: NAICS {naics} → HTTP {resp.status_code}")
                if resp.status_code == 429: print("Rate limit hit"); break
                if resp.status_code != 200: continue

                data = resp.json()
                for s in data.get("opportunitiesData", []):
                    notice_id = s.get("noticeId", "")
                    if notice_id in existing_notice_ids: continue
                    existing_notice_ids.add(notice_id)

                    sa_code = s.get("typeOfSetAside") or ""
                    set_aside = SET_ASIDE_MAP.get(sa_code, sa_code or "Full & Open")
                    pop = s.get("placeOfPerformance", {}) or {}
                    pop_state = ""
                    if pop:
                        state_obj = pop.get("state", {}) or {}
                        pop_state = state_obj.get("code", "") if isinstance(state_obj, dict) else ""

                    deadline = s.get("responseDeadLine") or ""
                    due_date = ""
                    if deadline:
                        for fmt in ["%Y-%m-%d", "%m/%d/%Y"]:
                            try: due_date = datetime.strptime(deadline[:10], fmt).strftime("%Y-%m-%d"); break
                            except: pass
                    if due_date:
                        try:
                            if datetime.strptime(due_date, "%Y-%m-%d").date() < date.today(): continue
                        except: pass

                    award = s.get("award", {}) or {}
                    try: value = float(award.get("amount", 0) or 0)
                    except: value = 0

                    all_opps.append({
                        "id": f"sam-{uuid.uuid4().hex[:8]}", "company_id": company["id"],
                        "title": (s.get("title") or "Untitled").strip(),
                        "agency": s.get("fullParentPathName", "") or s.get("department", "") or "",
                        "naics": s.get("naicsCode") or naics, "location": pop_state or "",
                        "due_date": due_date, "value": value, "set_aside": set_aside,
                        "scope": (s.get("description", "") or s.get("title", "") or "")[:2000],
                        "status": "new", "source": "sam.gov", "sam_notice_id": notice_id,
                        "sam_sol_number": s.get("solicitationNumber", ""),
                        "sam_posted_date": s.get("postedDate", ""),
                        "sam_type": s.get("type", ""),
                        "sam_link": f"https://sam.gov/opp/{notice_id}/view" if notice_id else "",
                    })
            except Exception as e:
                print(f"SAM.gov error for NAICS {naics}: {e}")
    print(f"SAM.gov complete: {calls_made} calls, {len(all_opps)} unique opps")
    return all_opps


# ── Auto-refresh ──
AUTO_REFRESH_HOURS = 6
auto_refresh_status = {"last_run": None, "next_run": None, "last_result": None, "running": False}

async def auto_refresh_loop():
    while True:
        await asyncio.sleep(10)
        session = SessionLocal()
        try:
            for comp in session.query(Company).all():
                cd = company_to_dict(comp)
                if not cd.get("sam_api_key"): continue
                auto_refresh_status["running"] = True
                auto_refresh_status["last_run"] = datetime.now().isoformat()
                try:
                    new_opps = await fetch_sam_opportunities(cd, days_back=30)
                    scored_added = 0
                    notify_opps = []
                    notify_min = cd.get("notify_min_score", 75)
                    for o in new_opps:
                        s = score_opportunity(o, cd)
                        if s["score"] >= 40:
                            db_opp = Opportunity(**{k: v for k, v in o.items()})
                            session.add(db_opp)
                            scored_added += 1
                            if s["score"] >= notify_min:
                                notify_opps.append({**o, **s})
                    session.commit()
                    if notify_opps: await send_notifications(cd, notify_opps)
                    auto_refresh_status["last_result"] = f"Added {scored_added} for {cd['name']}"
                    print(f"[AUTO-REFRESH] {cd['name']}: {len(new_opps)} found, {scored_added} added, {len(notify_opps)} notified")
                except Exception as e:
                    session.rollback()
                    auto_refresh_status["last_result"] = f"Error: {str(e)[:100]}"
                    print(f"[AUTO-REFRESH] Error: {e}")
                auto_refresh_status["running"] = False
        finally:
            session.close()
        auto_refresh_status["next_run"] = (datetime.now() + timedelta(hours=AUTO_REFRESH_HOURS)).isoformat()
        await asyncio.sleep(AUTO_REFRESH_HOURS * 3600)


@asynccontextmanager
async def lifespan(app):
    init_db()
    task = asyncio.create_task(auto_refresh_loop())
    print(f"[AUTO-REFRESH] Started — every {AUTO_REFRESH_HOURS} hours")
    yield
    task.cancel()

app = FastAPI(title="ConstructBid AI", version="4.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Models ──
class CompanyUpdate(BaseModel):
    name: str; services: list[str]; certifications: list[str]; naics: list[str]
    bonding_capacity: float; regions: list[str]; sam_api_key: Optional[str] = ""
    notify_email: Optional[str] = ""; notify_phone: Optional[str] = ""
    notify_enabled: Optional[bool] = False; notify_min_score: Optional[int] = 75

class OpportunityCreate(BaseModel):
    title: str; agency: str; naics: str; location: str; due_date: str
    value: float; set_aside: str; scope: str

class FieldReportRequest(BaseModel):
    project_name: str; notes: str

class ProposalRequest(BaseModel):
    section: str; opportunity_id: str

class SAMFetchRequest(BaseModel):
    days_back: Optional[int] = 30; min_score: Optional[int] = 40

class VoiceInput(BaseModel):
    transcript: str


# ── Proposal Templates ──
def generate_proposal(section, opp, company, past):
    t = {
        "executive": f"EXECUTIVE SUMMARY — DRAFT\n\n{company['name']} is pleased to submit this proposal for the {opp['title']} project in response to {opp.get('agency','')}.\n\nAs a certified {', '.join(company.get('certifications',[]))} firm, {company['name']} brings proven experience in {', '.join(company.get('services','')[:3])} with a strong track record.\n\nWith bonding capacity of ${company.get('bonding_capacity',0)/1e6:.1f}M and operations across {', '.join(company.get('regions',[]))}, we are well-positioned.\n\n[ADD: Approach summary]\n[ADD: Key differentiators]\n[ADD: Timeline]",
        "technical": f"TECHNICAL APPROACH — DRAFT\n\nProject Understanding:\n{opp.get('scope','[Not provided]')}\n\nPhase 1 — Mobilization & Planning\n- Site assessment\n- Work plan and schedule\n- Safety and QC plans\n- Subcontractor coordination\n\nPhase 2 — Execution\n- [ADD: Specific tasks]\n- QC inspections\n- Daily reporting\n- Coordination with operations\n\nPhase 3 — Closeout\n- Final inspections\n- As-built documentation\n- Warranty coordination\n- Site restoration",
        "pastPerformance": "PAST PERFORMANCE — DRAFT\n\n" + ("\n\n".join([f"{i+1}. {p['name']}\n   Client: {p['client']}\n   Value: ${p['value']:,.0f}\n   Year: {p['year']}\n   Scope: {p['scope']}\n   [ADD: Reference contact]" for i, p in enumerate(past)]) if past else "[No past projects loaded yet]"),
        "staffing": "STAFFING PLAN — DRAFT\n\n1. Project Manager — [Name, qualifications]\n2. Site Superintendent — [Name]\n3. Safety Officer — [OSHA certs]\n4. QC Manager — [Certifications]\n\n[ADD: Org chart]\n[ADD: Resumes]",
        "compliance": "COMPLIANCE CHECKLIST — DRAFT\n\n" + "\n".join([f"[ ] {c} current" for c in company.get("certifications",[])]) + f"\n[ ] NAICS {opp.get('naics','')} confirmed\n[ ] Bonding sufficient (${company.get('bonding_capacity',0)/1e6:.1f}M)\n[ ] Insurance current\n[ ] Licenses for {opp.get('location','')} obtained\n[ ] Past performance refs ready\n[ ] Safety plan prepared\n[ ] QC plan prepared\n[ ] Wage determination reviewed\n[ ] All amendments acknowledged",
    }
    return t.get(section, "Section not found.")


# ── Routes ──

@app.get("/")
def root():
    return {"app": "ConstructBid AI", "version": "4.0.0", "status": "running", "auto_refresh": auto_refresh_status}

@app.get("/api/company/{company_id}")
def get_company(company_id: str):
    session = SessionLocal()
    try:
        c = session.query(Company).filter(Company.id == company_id).first()
        if not c: raise HTTPException(404, "Company not found")
        d = company_to_dict(c)
        key = d.get("sam_api_key", "")
        d["sam_api_key_set"] = bool(key)
        d["sam_api_key_preview"] = key[:6] + "..." if len(key) > 6 else key
        return d
    finally:
        session.close()

@app.put("/api/company/{company_id}")
def update_company(company_id: str, data: CompanyUpdate):
    session = SessionLocal()
    try:
        c = session.query(Company).filter(Company.id == company_id).first()
        if not c: raise HTTPException(404, "Company not found")
        for k, v in data.dict().items():
            setattr(c, k, v)
        c.updated_at = datetime.utcnow()
        session.commit()
        return company_to_dict(c)
    finally:
        session.close()

@app.get("/api/opportunities/{company_id}")
def list_opportunities(company_id: str):
    session = SessionLocal()
    try:
        c = session.query(Company).filter(Company.id == company_id).first()
        cd = company_to_dict(c) if c else {}
        opps = session.query(Opportunity).filter(Opportunity.company_id == company_id).all()
        results = []
        for o in opps:
            od = opp_to_dict(o)
            s = score_opportunity(od, cd)
            results.append({**od, **s})
        return sorted(results, key=lambda x: x["score"], reverse=True)
    finally:
        session.close()

@app.post("/api/opportunities/{company_id}")
def create_opportunity(company_id: str, data: OpportunityCreate):
    session = SessionLocal()
    try:
        opp = Opportunity(id=f"opp-{uuid.uuid4().hex[:8]}", company_id=company_id, **data.dict(), source="manual")
        session.add(opp)
        session.commit()
        c = session.query(Company).filter(Company.id == company_id).first()
        od = opp_to_dict(opp)
        return {**od, **score_opportunity(od, company_to_dict(c) if c else {})}
    finally:
        session.close()

@app.delete("/api/opportunities/{opportunity_id}")
def delete_opportunity(opportunity_id: str):
    session = SessionLocal()
    try:
        opp = session.query(Opportunity).filter(Opportunity.id == opportunity_id).first()
        if not opp: raise HTTPException(404, "Not found")
        session.delete(opp)
        session.commit()
        return {"deleted": True}
    finally:
        session.close()

@app.post("/api/sam-refresh/{company_id}")
async def sam_refresh(company_id: str, req: SAMFetchRequest):
    session = SessionLocal()
    try:
        c = session.query(Company).filter(Company.id == company_id).first()
        if not c: raise HTTPException(404, "Company not found")
        cd = company_to_dict(c)
        if not cd.get("sam_api_key"): raise HTTPException(400, "SAM.gov API key not set.")

        old_count = session.query(Opportunity).filter(Opportunity.source == "sam.gov", Opportunity.company_id == company_id).count()
        session.query(Opportunity).filter(Opportunity.source == "sam.gov", Opportunity.company_id == company_id).delete()
        session.commit()
    finally:
        session.close()

    new_opps = await fetch_sam_opportunities(cd, req.days_back)
    scored = []
    added = 0
    session = SessionLocal()
    try:
        for o in new_opps:
            s = score_opportunity(o, cd)
            if s["score"] >= req.min_score:
                scored.append({**o, **s})
                session.add(Opportunity(**{k: v for k, v in o.items()}))
                added += 1
        session.commit()
    finally:
        session.close()

    return {"cleared": old_count, "fetched": len(new_opps), "added": added, "min_score_filter": req.min_score, "opportunities": sorted(scored, key=lambda x: x["score"], reverse=True)}

@app.post("/api/clear-expired/{company_id}")
def clear_expired(company_id: str):
    session = SessionLocal()
    try:
        today_str = date.today().strftime("%Y-%m-%d")
        opps = session.query(Opportunity).filter(Opportunity.company_id == company_id, Opportunity.due_date < today_str, Opportunity.due_date != "").all()
        removed = len(opps)
        for o in opps: session.delete(o)
        session.commit()
        remaining = session.query(Opportunity).filter(Opportunity.company_id == company_id).count()
        return {"removed": removed, "remaining": remaining}
    finally:
        session.close()

@app.post("/api/clear-passes/{company_id}")
def clear_passes(company_id: str):
    session = SessionLocal()
    try:
        c = session.query(Company).filter(Company.id == company_id).first()
        cd = company_to_dict(c) if c else {}
        opps = session.query(Opportunity).filter(Opportunity.company_id == company_id, Opportunity.source == "sam.gov").all()
        removed = 0
        for o in opps:
            s = score_opportunity(opp_to_dict(o), cd)
            if s["recommendation"] == "PASS":
                session.delete(o); removed += 1
        session.commit()
        remaining = session.query(Opportunity).filter(Opportunity.company_id == company_id).count()
        return {"removed": removed, "remaining": remaining}
    finally:
        session.close()

@app.get("/api/score/{opportunity_id}")
def score(opportunity_id: str):
    session = SessionLocal()
    try:
        o = session.query(Opportunity).filter(Opportunity.id == opportunity_id).first()
        if not o: raise HTTPException(404, "Not found")
        c = session.query(Company).filter(Company.id == o.company_id).first()
        return score_opportunity(opp_to_dict(o), company_to_dict(c) if c else {})
    finally:
        session.close()

@app.post("/api/proposal")
def create_proposal(req: ProposalRequest):
    session = SessionLocal()
    try:
        o = session.query(Opportunity).filter(Opportunity.id == req.opportunity_id).first()
        if not o: raise HTTPException(404, "Not found")
        c = session.query(Company).filter(Company.id == o.company_id).first()
        ps = [proj_to_dict(p) for p in session.query(Project).filter(Project.company_id == o.company_id).all()]
        return {"section": req.section, "content": generate_proposal(req.section, opp_to_dict(o), company_to_dict(c) if c else {}, ps)}
    finally:
        session.close()

@app.post("/api/field-report")
def create_field_report(req: FieldReportRequest):
    today = datetime.now().strftime("%A, %B %d, %Y")
    return {"report": f"DAILY FIELD REPORT\n{'━'*40}\nDate: {today}\nProject: {req.project_name}\nPrepared by: [Superintendent Name]\nWeather: [Enter conditions]\n\n─── WORK PERFORMED TODAY ───\n{req.notes}\n\n─── LABOR ON SITE ───\n• Company crew: [#]\n• Subcontractors: [List]\n• Total manhours: [#]\n\n─── ISSUES / DELAYS ───\n• [Describe any issues]\n\n─── SAFETY ───\n• Incidents: None\n• Toolbox talk: [Topic]\n\n─── TOMORROW'S PLAN ───\n• [Planned activities]\n{'━'*40}"}

@app.get("/api/projects/{company_id}")
def list_projects(company_id: str):
    session = SessionLocal()
    try:
        return [proj_to_dict(p) for p in session.query(Project).filter(Project.company_id == company_id).all()]
    finally:
        session.close()

@app.get("/api/auto-refresh-status")
def get_auto_refresh_status():
    return auto_refresh_status

@app.post("/api/test-notification/{company_id}")
async def test_notification(company_id: str):
    session = SessionLocal()
    try:
        c = session.query(Company).filter(Company.id == company_id).first()
        if not c: raise HTTPException(404, "Not found")
        cd = company_to_dict(c)
        if not cd.get("notify_enabled"): raise HTTPException(400, "Notifications not enabled.")
        if not cd.get("notify_email") and not cd.get("notify_phone"): raise HTTPException(400, "No email or phone set.")
    finally:
        session.close()
    test_opp = {"title": "TEST — Test Notification", "agency": "ConstructBid AI Test", "score": 99, "recommendation": "PURSUE", "value": 2500000, "due_date": "2026-05-01", "set_aside": "SDVOSB", "naics": "236220", "location": "VA", "sam_link": "https://sam.gov"}
    await send_notifications(cd, [test_opp])
    targets = []
    if cd.get("notify_email"): targets.append(f"email ({cd['notify_email']})")
    if cd.get("notify_phone"): targets.append(f"SMS ({cd['notify_phone']})")
    return {"sent_to": targets}


# ── Voice AI ──
VOICE_PARSE_PROMPT = """You are parsing a spoken company description into structured data for a government contracting platform. The speech-to-text may have errors — use your best judgment to correct them.

Extract these fields from the transcript. Return ONLY valid JSON, no markdown, no explanation:

{
  "name": "Company name (correct speech-to-text errors)",
  "services": ["list of services"],
  "certifications": ["SDVOSB, VOSB, 8(a), HUBZone, WOSB, OSHA, etc. Note: speech recognition often mangles SDVOSB into STV OSB, SD VOSB, etc."],
  "regions": ["2-letter US state codes"],
  "bonding_capacity": 0,
  "naics": ["6-digit NAICS codes if mentioned"]
}

Rules:
- bonding_capacity: dollar amount as plain number. "5 million" = 5000000. Ignore rankings/years/counts.
- certifications: Any mention of veteran owned / service disabled / SDVOSB (even misspelled) → "SDVOSB"
- regions: state names → 2-letter codes
- name: the actual company name, NOT phrases like "my company"
- Only include fields with data. Return ONLY JSON."""

@app.post("/api/parse-voice-profile")
async def parse_voice_profile(data: VoiceInput):
    anthropic_key = load_env_var("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post("https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": anthropic_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={"model": "claude-sonnet-4-20250514", "max_tokens": 1000, "messages": [{"role": "user", "content": VOICE_PARSE_PROMPT + "\n\nTranscript:\n" + data.transcript}]})
                if resp.status_code == 200:
                    ai_text = "".join(b["text"] for b in resp.json().get("content", []) if b.get("type") == "text")
                    ai_text = re.sub(r'^```(?:json)?\s*', '', ai_text.strip())
                    ai_text = re.sub(r'\s*```$', '', ai_text)
                    parsed = json.loads(ai_text)
                    result = {k: v for k, v in parsed.items() if v}
                    if "bonding_capacity" in result: result["bonding_capacity"] = int(result["bonding_capacity"])
                    return {"parsed": result, "transcript": data.transcript, "fields_found": len(result), "method": "ai"}
        except Exception as e:
            print(f"Claude AI parse error: {e}")

    # Fallback: keyword parsing
    text = data.transcript.lower()
    result = {}
    raw = data.transcript.strip()
    for sep in [" is ", " we ", " our ", ". "]:
        if sep.lower() in raw.lower():
            candidate = raw[:raw.lower().index(sep.lower())].strip()
            if 2 < len(candidate) < 80: result["name"] = candidate; break

    svc_map = {"construction":"General Construction","cemetery":"Cemetery Operations","facilities":"Facilities Maintenance","hvac":"HVAC/Plumbing","plumbing":"HVAC/Plumbing","landscaping":"Landscaping","demolition":"Demolition","renovation":"Renovations","design build":"Design-Build","restoration":"Historical Restorations","roofing":"Roofing","electrical":"Electrical","paving":"Paving","concrete":"Concrete"}
    found_svc = list({v for k, v in svc_map.items() if k in text})
    if found_svc: result["services"] = found_svc

    cert_map = {"sdvosb":"SDVOSB","stv osb":"SDVOSB","sd vosb":"SDVOSB","service disabled veteran":"SDVOSB","veteran owned":"SDVOSB","8a":"8(a)","hubzone":"HUBZone","woman owned":"WOSB","osha":"OSHA 30","mentor protege":"SBA Mentor-Protégé"}
    found_cert = list({v for k, v in cert_map.items() if k in text})
    if found_cert: result["certifications"] = found_cert

    state_map = {"virginia":"VA","maryland":"MD","ohio":"OH","michigan":"MI","indiana":"IN","oklahoma":"OK","nebraska":"NE","california":"CA","tennessee":"TN","wyoming":"WY","texas":"TX","florida":"FL","georgia":"GA","maine":"ME","new york":"NY","north carolina":"NC","south carolina":"SC","west virginia":"WV","washington dc":"DC","pennsylvania":"PA","illinois":"IL","colorado":"CO","arizona":"AZ","oregon":"OR","washington":"WA","alabama":"AL","kentucky":"KY","missouri":"MO"}
    found_st = [v for k, v in state_map.items() if k in text]
    if found_st: result["regions"] = found_st

    for pat in [r'bonding.*?(\d+)\s*(?:million|mil)', r'(\d+)\s*(?:million|mil)\s*(?:dollar|bond|per\s*contract)']:
        m = re.search(pat, text)
        if m: result["bonding_capacity"] = int(float(m.group(1)) * 1e6); break

    naics = re.findall(r'\b(\d{6})\b', data.transcript)
    if naics: result["naics"] = list(set(naics))

    return {"parsed": result, "transcript": data.transcript, "fields_found": len(result), "method": "keywords"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
