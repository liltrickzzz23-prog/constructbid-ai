"""
ConstructBid AI — Government Contractor OS
FastAPI backend with SAM.gov integration, auto-refresh, scoring, proposal generation, and field reports.
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import json, os, uuid, httpx, asyncio
from datetime import datetime, date, timedelta
from contextlib import asynccontextmanager

# ── Notification System ──
async def send_notifications(company: dict, new_pursue_opps: list):
    """Send email and/or SMS alerts for new high-scoring opportunities."""
    if not company.get("notify_enabled"):
        return
    if not new_pursue_opps:
        return

    email = company.get("notify_email", "")
    phone = company.get("notify_phone", "")
    comp_name = company.get("name", "Your Company")

    # Build message
    count = len(new_pursue_opps)
    subject = f"🔥 {count} New High-Score Opportunit{'ies' if count > 1 else 'y'} Found — ConstructBid AI"

    text_lines = [f"ConstructBid AI found {count} new opportunit{'ies' if count > 1 else 'y'} for {comp_name}:\n"]
    for opp in new_pursue_opps[:5]:  # max 5 in notification
        score = opp.get("score", 0)
        title = opp.get("title", "Untitled")
        agency = opp.get("agency", "")
        value = opp.get("value", 0)
        due = opp.get("due_date", "TBD")
        link = opp.get("sam_link", "")
        val_str = f"${value/1e6:.1f}M" if value else "TBD"
        text_lines.append(f"• [{score} pts] {title}")
        text_lines.append(f"  Agency: {agency}")
        text_lines.append(f"  Value: {val_str} | Due: {due}")
        if link:
            text_lines.append(f"  {link}")
        text_lines.append("")

    if count > 5:
        text_lines.append(f"... and {count - 5} more. Open your dashboard to see all.")

    plain_text = "\n".join(text_lines)

    # HTML version for email
    html_rows = ""
    for opp in new_pursue_opps[:5]:
        score = opp.get("score", 0)
        rec = opp.get("recommendation", "PURSUE")
        color = "#22c55e" if rec == "PURSUE" else "#f59e0b"
        title = opp.get("title", "Untitled")
        agency = opp.get("agency", "")
        value = opp.get("value", 0)
        due = opp.get("due_date", "TBD")
        link = opp.get("sam_link", "")
        val_str = f"${value/1e6:.1f}M" if value else "TBD"
        html_rows += f"""
        <tr style="border-bottom:1px solid #1e2d3d">
          <td style="padding:12px;text-align:center"><span style="display:inline-block;width:44px;height:44px;border-radius:50%;border:3px solid {color};line-height:38px;text-align:center;font-weight:700;color:{color};font-size:14px">{score}</span></td>
          <td style="padding:12px">
            <strong style="color:#e2e8f0;font-size:14px">{title}</strong><br>
            <span style="color:#64748b;font-size:12px">{agency}</span><br>
            <span style="color:#64748b;font-size:12px">{val_str} · Due {due}</span>
            {'<br><a href="'+link+'" style="color:#3b82f6;font-size:12px">View on SAM.gov →</a>' if link else ''}
          </td>
        </tr>"""

    html_body = f"""
    <div style="background:#0a0f1a;padding:20px;font-family:Arial,sans-serif">
      <div style="max-width:600px;margin:0 auto;background:#111827;border-radius:12px;overflow:hidden;border:1px solid #1e2d3d">
        <div style="background:linear-gradient(135deg,#059669,#06b6d4);padding:20px;text-align:center">
          <h1 style="color:white;margin:0;font-size:20px">🔥 {count} New Opportunit{'ies' if count > 1 else 'y'} Found</h1>
          <p style="color:rgba(255,255,255,0.8);margin:8px 0 0;font-size:14px">ConstructBid AI — {comp_name}</p>
        </div>
        <table style="width:100%;border-collapse:collapse">{html_rows}</table>
        {f'<p style="padding:12px;color:#64748b;font-size:13px;text-align:center">+ {count-5} more in your dashboard</p>' if count > 5 else ''}
        <div style="padding:20px;text-align:center">
          <a href="http://127.0.0.1:8000" style="display:inline-block;background:#3b82f6;color:white;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px">Open Dashboard →</a>
        </div>
      </div>
    </div>"""

    # ── Send Email via Resend ──
    if email:
        resend_key = os.environ.get("RESEND_API_KEY", "")
        if not resend_key:
            env_path = os.path.join(DATA_DIR, "..", ".env")
            if os.path.exists(env_path):
                with open(env_path) as f:
                    for line in f:
                        if line.strip().startswith("RESEND_API_KEY="):
                            resend_key = line.strip().split("=", 1)[1].strip().strip('"').strip("'")

        if resend_key:
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(
                        "https://api.resend.com/emails",
                        headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
                        json={
                            "from": "ConstructBid AI <onboarding@resend.dev>",
                            "to": [email],
                            "subject": subject,
                            "html": html_body,
                            "text": plain_text,
                        }
                    )
                    if resp.status_code in (200, 201):
                        print(f"[NOTIFY] Email sent to {email}")
                    else:
                        print(f"[NOTIFY] Email failed: {resp.status_code} — {resp.text[:200]}")
            except Exception as e:
                print(f"[NOTIFY] Email error: {e}")
        else:
            print(f"[NOTIFY] No RESEND_API_KEY set — skipping email to {email}")

    # ── Send SMS via Twilio ──
    if phone:
        twilio_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
        twilio_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        twilio_from = os.environ.get("TWILIO_FROM_NUMBER", "")

        if not twilio_sid:
            env_path = os.path.join(DATA_DIR, "..", ".env")
            if os.path.exists(env_path):
                with open(env_path) as f:
                    for line in f:
                        l = line.strip()
                        if l.startswith("TWILIO_ACCOUNT_SID="): twilio_sid = l.split("=", 1)[1].strip().strip('"').strip("'")
                        if l.startswith("TWILIO_AUTH_TOKEN="): twilio_token = l.split("=", 1)[1].strip().strip('"').strip("'")
                        if l.startswith("TWILIO_FROM_NUMBER="): twilio_from = l.split("=", 1)[1].strip().strip('"').strip("'")

        if twilio_sid and twilio_token and twilio_from:
            # SMS body (160 char limit per segment)
            sms = f"ConstructBid AI: {count} new high-score opportunit{'ies' if count > 1 else 'y'}!\n"
            top = new_pursue_opps[0]
            sms += f"Top: {top.get('title','')[:60]} ({top.get('score',0)} pts)"
            if count > 1:
                sms += f"\n+{count-1} more — check your dashboard"

            try:
                import base64
                auth = base64.b64encode(f"{twilio_sid}:{twilio_token}".encode()).decode()
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(
                        f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json",
                        headers={"Authorization": f"Basic {auth}"},
                        data={"To": phone, "From": twilio_from, "Body": sms}
                    )
                    if resp.status_code in (200, 201):
                        print(f"[NOTIFY] SMS sent to {phone}")
                    else:
                        print(f"[NOTIFY] SMS failed: {resp.status_code} — {resp.text[:200]}")
            except Exception as e:
                print(f"[NOTIFY] SMS error: {e}")
        else:
            print(f"[NOTIFY] Twilio not configured — skipping SMS to {phone}")


# ── Auto-refresh background task ──
AUTO_REFRESH_HOURS = 6
auto_refresh_status = {"last_run": None, "next_run": None, "last_result": None, "running": False}

async def auto_refresh_loop():
    """Background loop that refreshes SAM.gov opportunities automatically."""
    while True:
        await asyncio.sleep(10)  # wait 10s after startup before first run
        for comp in companies:
            if not comp.get("sam_api_key"):
                continue
            auto_refresh_status["running"] = True
            auto_refresh_status["last_run"] = datetime.now().isoformat()
            try:
                new_opps = await fetch_sam_opportunities(comp, days_back=30)
                scored_added = 0
                notify_opps = []
                existing_ids = {o.get("sam_notice_id") for o in opportunities if o.get("sam_notice_id")}
                notify_min = comp.get("notify_min_score", 75)

                for o in new_opps:
                    if o.get("sam_notice_id") in existing_ids:
                        continue
                    s = score_opportunity(o, comp)
                    if s["score"] >= 40:
                        opportunities.append(o)
                        scored_added += 1
                        # Track high-scorers for notifications
                        if s["score"] >= notify_min:
                            notify_opps.append({**o, **s})

                if scored_added > 0:
                    save_json("opportunities", opportunities)

                # Send notifications for high-scoring new opportunities
                if notify_opps:
                    await send_notifications(comp, notify_opps)

                auto_refresh_status["last_result"] = f"Added {scored_added} new opportunities for {comp['name']}"
                print(f"[AUTO-REFRESH] {comp['name']}: found {len(new_opps)}, added {scored_added}, notified {len(notify_opps)}")
            except Exception as e:
                auto_refresh_status["last_result"] = f"Error: {str(e)[:100]}"
                print(f"[AUTO-REFRESH] Error for {comp['name']}: {e}")
            auto_refresh_status["running"] = False
        
        auto_refresh_status["next_run"] = (datetime.now() + timedelta(hours=AUTO_REFRESH_HOURS)).isoformat()
        await asyncio.sleep(AUTO_REFRESH_HOURS * 3600)

@asynccontextmanager
async def lifespan(app):
    # Start background auto-refresh
    task = asyncio.create_task(auto_refresh_loop())
    print(f"[AUTO-REFRESH] Started — will check SAM.gov every {AUTO_REFRESH_HOURS} hours")
    yield
    task.cancel()

app = FastAPI(title="ConstructBid AI", version="3.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Storage ──
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

def load_json(name, default):
    path = os.path.join(DATA_DIR, f"{name}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default

def save_json(name, data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, f"{name}.json"), "w") as f:
        json.dump(data, f, indent=2, default=str)

# ── Data ──
companies = load_json("companies", [
    {
        "id": "default",
        "name": "Your Company Name",
        "services": ["General Construction", "Facilities Maintenance", "Cemetery Operations",
                      "Site Preparation", "HVAC/Plumbing", "Landscaping", "Design-Build"],
        "certifications": ["SDVOSB", "OSHA 30", "EPA Lead-Safe"],
        "naics": ["236220", "236210", "237110", "237310", "238220", "561730"],
        "bonding_capacity": 5000000,
        "regions": ["VA", "MD", "DC", "NC", "WV"],
        "sam_api_key": "",
        "notify_email": "",
        "notify_phone": "",
        "notify_enabled": False,
        "notify_min_score": 75,
    }
])

opportunities = load_json("opportunities", [
    {"id": "opp-1", "company_id": "default", "title": "National Cemetery Columbarium Expansion",
     "agency": "Department of Veterans Affairs", "naics": "236220", "location": "VA",
     "due_date": "2026-05-15", "value": 2800000, "set_aside": "SDVOSB",
     "scope": "Construction of new columbarium courts including site prep, foundations, granite installation, landscaping, and irrigation systems.",
     "status": "new", "source": "manual", "sam_notice_id": None},
    {"id": "opp-2", "company_id": "default", "title": "HVAC System Replacement – Federal Building",
     "agency": "General Services Administration", "naics": "238220", "location": "DC",
     "due_date": "2026-05-02", "value": 950000, "set_aside": "Small Business",
     "scope": "Complete replacement of rooftop HVAC units, ductwork modifications, controls upgrade, and commissioning for a 45,000 SF federal office building.",
     "status": "new", "source": "manual", "sam_notice_id": None},
])

projects = load_json("projects", [
    {"id": "proj-1", "company_id": "default", "name": "Abraham Lincoln National Cemetery – Section 40 Expansion",
     "client": "NCA / VA", "value": 3100000, "year": 2024,
     "scope": "Gravesite expansion with 5,000 new burial sites, roads, drainage, irrigation"},
    {"id": "proj-2", "company_id": "default", "name": "Fort Belvoir – Building 1442 Renovation",
     "client": "US Army", "value": 1800000, "year": 2023,
     "scope": "Complete interior renovation, HVAC replacement, ADA upgrades"},
])


# ── Models ──
class CompanyUpdate(BaseModel):
    name: str
    services: list[str]
    certifications: list[str]
    naics: list[str]
    bonding_capacity: float
    regions: list[str]
    sam_api_key: Optional[str] = ""
    notify_email: Optional[str] = ""
    notify_phone: Optional[str] = ""
    notify_enabled: Optional[bool] = False
    notify_min_score: Optional[int] = 75

class OpportunityCreate(BaseModel):
    title: str
    agency: str
    naics: str
    location: str
    due_date: str
    value: float
    set_aside: str
    scope: str

class FieldReportRequest(BaseModel):
    project_name: str
    notes: str

class ProposalRequest(BaseModel):
    section: str
    opportunity_id: str

class SAMFetchRequest(BaseModel):
    days_back: Optional[int] = 30
    min_score: Optional[int] = 30  # show anything potentially relevant


# ── Scoring Engine v2 ──
# Designed for reliability with SAM.gov data where scope/description is often
# missing. Prioritizes hard data (NAICS, set-aside, location) over text matching.
#
# Weights:
#   NAICS match:         35 pts  (always available from SAM.gov)
#   Set-aside eligibility: 25 pts  (hard disqualifier if you can't bid)
#   Scope/title keywords:  20 pts  (scans both title + scope)
#   Location fit:          10 pts  (always available from SAM.gov)
#   Bonding/size fit:       5 pts
#   Timeline:               5 pts
#   Total possible:       100 pts
#
# Key design decisions:
#   - Set-aside mismatch CAPS the score at 35 (you literally can't win)
#   - Expired deadlines CAP the score at 25
#   - NAICS match on the 4-digit prefix gives partial credit (related industry)
#   - Keywords are derived from the company's services + construction terms
#   - Title is scanned alongside scope since SAM.gov often omits descriptions

# NAICS categories — 2-digit prefix groupings for partial credit
NAICS_GROUPS = {
    "23": "construction",
    "56": "facilities_services",
    "53": "real_estate",
    "81": "cemetery_services",
    "49": "warehousing",
    "48": "transportation",
}

# Master keyword bank for construction/facilities scoring
SCOPE_KEYWORDS = [
    # Cemetery-specific (high signal)
    "cemetery", "columbarium", "gravesite", "burial", "headstone", "niche",
    "memorial", "interment", "cremation", "mausoleum",
    # Construction
    "construction", "renovation", "remodel", "expansion", "addition",
    "demolition", "design-build", "design build", "build-out",
    "general contractor", "tenant improvement",
    # Facilities
    "facilities", "maintenance", "janitorial", "custodial", "grounds",
    "landscaping", "mowing", "irrigation", "snow removal", "pest control",
    "building operations", "facility support",
    # Trades
    "hvac", "plumbing", "mechanical", "electrical", "roofing",
    "painting", "flooring", "concrete", "masonry", "carpentry",
    "fire protection", "fire alarm",
    # Site work
    "site prep", "site preparation", "excavation", "grading",
    "paving", "asphalt", "road repair", "drainage", "utilities",
    "fencing", "signage", "sidewalk", "curb",
    # Restoration
    "restoration", "historic", "historical", "rehabilitation", "remediation",
    # Real estate / leasing
    "lease", "leasing", "outpatient clinic", "cboc", "medical center",
    "office space",
    # General high-value terms
    "repair", "replace", "install", "upgrade", "improve", "modify",
]

def score_opportunity(opp: dict, company: dict) -> dict:
    score = 0
    reasons = []
    flags = []  # hard disqualifiers

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
    opp_title = (opp.get("title", "") or "").lower()
    opp_scope = (opp.get("scope", "") or "").lower()
    opp_text = opp_title + " " + opp_scope  # search both

    # ── 1. NAICS Match (35 pts) ──
    if opp_naics in comp_naics:
        score += 35
        reasons.append(f"✓ NAICS {opp_naics} — exact match to your capabilities")
    elif opp_naics[:4] and any(n[:4] == opp_naics[:4] for n in comp_naics):
        score += 25
        reasons.append(f"◐ NAICS {opp_naics} — closely related to your codes (same subsector)")
    elif opp_naics[:3] and any(n[:3] == opp_naics[:3] for n in comp_naics):
        score += 15
        reasons.append(f"○ NAICS {opp_naics} — same industry group but different specialty")
    elif opp_naics[:2] and any(n[:2] == opp_naics[:2] for n in comp_naics):
        score += 8
        reasons.append(f"△ NAICS {opp_naics} — same sector but weak alignment")
    else:
        score += 0
        reasons.append(f"✗ NAICS {opp_naics} — outside your registered capabilities")

    # ── 2. Set-Aside Eligibility (25 pts) ──
    # This is the #1 disqualifier — if you can't bid, nothing else matters
    sa_upper = opp_set_aside.upper().strip()
    has_sdvosb = any("SDVOSB" in c for c in comp_certs)
    has_vosb = any("VOSB" in c for c in comp_certs) or has_sdvosb
    has_8a = any("8(A)" in c or "8A" in c for c in comp_certs)
    has_hubzone = any("HUBZONE" in c for c in comp_certs)
    has_wosb = any("WOSB" in c or "EDWOSB" in c for c in comp_certs)

    sa_eligible = False
    if not sa_upper or sa_upper in ("FULL & OPEN", "FULL AND OPEN", "NONE", "N/A"):
        score += 15
        reasons.append("○ Full & open competition — anyone can bid, but more competitors")
        sa_eligible = True
    elif "SDVOSB" in sa_upper:
        if has_sdvosb:
            score += 25
            reasons.append("✓ SDVOSB set-aside — you are certified, limited competition")
            sa_eligible = True
        else:
            score += 0
            reasons.append("✗ SDVOSB set-aside — you are NOT certified (cannot bid)")
            flags.append("set_aside_disqualified")
    elif "VOSB" in sa_upper and "SDVOSB" not in sa_upper:
        if has_vosb:
            score += 25
            reasons.append("✓ VOSB set-aside — your SDVOSB qualifies you")
            sa_eligible = True
        else:
            score += 0
            reasons.append("✗ VOSB set-aside — you are NOT certified (cannot bid)")
            flags.append("set_aside_disqualified")
    elif "8(A)" in sa_upper or "8A" in sa_upper:
        if has_8a:
            score += 25
            reasons.append("✓ 8(a) set-aside — you are certified")
            sa_eligible = True
        else:
            score += 0
            reasons.append("✗ 8(a) set-aside — you are NOT certified (cannot bid)")
            flags.append("set_aside_disqualified")
    elif "HUBZONE" in sa_upper:
        if has_hubzone:
            score += 25
            reasons.append("✓ HUBZone set-aside — you are certified")
            sa_eligible = True
        else:
            score += 0
            reasons.append("✗ HUBZone set-aside — you are NOT certified (cannot bid)")
            flags.append("set_aside_disqualified")
    elif "WOSB" in sa_upper or "EDWOSB" in sa_upper:
        if has_wosb:
            score += 25
            reasons.append("✓ WOSB set-aside — you are certified")
            sa_eligible = True
        else:
            score += 0
            reasons.append("✗ WOSB set-aside — you are NOT certified (cannot bid)")
            flags.append("set_aside_disqualified")
    elif "SMALL BUSINESS" in sa_upper or "SBA" in sa_upper or "SMALL" in sa_upper:
        score += 20
        reasons.append("✓ Small business set-aside — you likely qualify")
        sa_eligible = True
    else:
        score += 10
        reasons.append(f"? Set-aside type '{opp_set_aside}' — verify your eligibility")
        sa_eligible = True

    # ── 3. Scope & Title Keywords (20 pts) ──
    # Build dynamic keywords from company services
    dynamic_kw = set()
    for svc in comp_services:
        for word in svc.split():
            if len(word) > 3:
                dynamic_kw.add(word.lower())

    # Combine with master keyword bank
    all_keywords = set(SCOPE_KEYWORDS) | dynamic_kw

    matched_kw = [k for k in all_keywords if k in opp_text]

    # Weight by specificity — cemetery/columbarium/burial are high-signal
    high_signal = ["cemetery", "columbarium", "gravesite", "burial", "memorial",
                   "interment", "niche", "headstone", "mausoleum"]
    high_matches = [k for k in high_signal if k in opp_text]

    if high_matches:
        kw_score = min(20, 12 + len(matched_kw) * 2)
        reasons.append(f"✓ Strong scope match: {', '.join(high_matches[:3])} (+{len(matched_kw)-len(high_matches)} more)")
    elif len(matched_kw) >= 4:
        kw_score = min(20, len(matched_kw) * 3)
        reasons.append(f"✓ Good scope alignment: {', '.join(list(matched_kw)[:5])}")
    elif len(matched_kw) >= 2:
        kw_score = min(14, len(matched_kw) * 4)
        reasons.append(f"◐ Partial scope match: {', '.join(list(matched_kw)[:4])}")
    elif len(matched_kw) == 1:
        kw_score = 5
        reasons.append(f"○ Weak scope match: {matched_kw[0]}")
    else:
        # No keywords matched but NAICS matched — scope might just be missing
        if opp_naics in comp_naics:
            kw_score = 8  # benefit of the doubt on NAICS match
            reasons.append("○ No scope details available — NAICS match suggests relevance")
        else:
            kw_score = 0
            reasons.append("✗ No scope keywords match your services")

    score += kw_score

    # ── 4. Location (10 pts) ──
    if not opp_location:
        score += 6
        reasons.append("○ Location not specified — may be remote or nationwide")
    elif opp_location in comp_regions:
        score += 10
        reasons.append(f"✓ Located in {opp_location} — your operating region")
    else:
        # Check if adjacent / reasonable travel distance
        # Simple adjacency map for common states
        adjacent = {
            "VA": ["MD", "DC", "WV", "NC", "TN", "KY"],
            "MD": ["VA", "DC", "WV", "PA", "DE"],
            "DC": ["VA", "MD"],
            "OH": ["MI", "IN", "KY", "WV", "PA"],
            "MI": ["OH", "IN", "WI"],
            "IN": ["MI", "OH", "IL", "KY"],
            "OK": ["TX", "KS", "AR", "MO"],
            "NE": ["KS", "SD", "IA", "CO", "WY"],
            "CA": ["OR", "NV", "AZ"],
            "TN": ["VA", "NC", "GA", "AL", "MS", "AR", "MO", "KY"],
            "WY": ["MT", "SD", "NE", "CO", "UT", "ID"],
        }
        is_adjacent = False
        for region in comp_regions:
            if opp_location in adjacent.get(region, []):
                is_adjacent = True
                break
        if is_adjacent:
            score += 5
            reasons.append(f"◐ Located in {opp_location} — adjacent to your region, reachable")
        else:
            score += 1
            reasons.append(f"△ Located in {opp_location} — outside your region, travel/mobilization cost")

    # ── 5. Bonding / Contract Size (5 pts) ──
    if opp_value <= 0:
        score += 4
        reasons.append("○ Contract value not disclosed")
    elif opp_value <= comp_bonding * 0.5:
        score += 5
        reasons.append(f"✓ ${opp_value/1e6:.1f}M — well within your bonding capacity")
    elif opp_value <= comp_bonding:
        score += 4
        reasons.append(f"✓ ${opp_value/1e6:.1f}M — within your bonding capacity")
    elif opp_value <= comp_bonding * 1.5:
        score += 2
        reasons.append(f"△ ${opp_value/1e6:.1f}M — near your bonding limit, may need partner")
    else:
        score += 0
        reasons.append(f"✗ ${opp_value/1e6:.1f}M — exceeds bonding capacity (${comp_bonding/1e6:.1f}M)")

    # ── 6. Timeline (5 pts) ──
    try:
        if opp_due:
            days_left = (datetime.strptime(opp_due, "%Y-%m-%d").date() - date.today()).days
            if days_left < 0:
                score += 0
                reasons.append("✗ Deadline has passed")
                flags.append("expired")
            elif days_left > 30:
                score += 5
                reasons.append(f"✓ {days_left} days until deadline — comfortable timeline")
            elif days_left > 14:
                score += 3
                reasons.append(f"◐ {days_left} days until deadline — tight but doable")
            elif days_left > 3:
                score += 1
                reasons.append(f"△ {days_left} days until deadline — rush effort required")
            else:
                score += 0
                reasons.append(f"✗ Only {days_left} day(s) left — extremely tight")
                flags.append("deadline_critical")
        else:
            score += 3
            reasons.append("○ No deadline specified")
    except:
        score += 3
        reasons.append("○ Could not determine deadline")

    # ── Apply Hard Caps ──
    score = min(100, score)

    # If you can't bid due to set-aside, cap score hard
    if "set_aside_disqualified" in flags:
        score = min(score, 35)
        reasons.insert(0, "⚠ DISQUALIFIED — set-aside requirement not met")

    # If deadline passed, cap score
    if "expired" in flags:
        score = min(score, 25)
        reasons.insert(0, "⚠ EXPIRED — response deadline has passed")

    # ── Recommendation ──
    if "set_aside_disqualified" in flags:
        rec = "PASS"
    elif "expired" in flags:
        rec = "PASS"
    elif score >= 75:
        rec = "PURSUE"
    elif score >= 55:
        rec = "REVIEW"
    else:
        rec = "PASS"

    return {"score": score, "recommendation": rec, "reasons": reasons}


# ── SAM.gov Integration ──
SAM_API_URL = "https://api.sam.gov/prod/opportunities/v2/search"

# Map set-aside codes from SAM.gov to readable names
SET_ASIDE_MAP = {
    "SBA": "Small Business",
    "SBP": "Small Business",
    "8A": "8(a)",
    "8AN": "8(a)",
    "HZC": "HUBZone",
    "HZS": "HUBZone",
    "SDVOSBC": "SDVOSB",
    "SDVOSBS": "SDVOSB",
    "VOSBC": "VOSB",
    "VOSBS": "VOSB",
    "WOSB": "WOSB",
    "WOSBSS": "WOSB",
    "EDWOSB": "EDWOSB",
    "": "Full & Open",
    None: "Full & Open",
}

async def fetch_sam_opportunities(company: dict, days_back: int = 30) -> list:
    """
    Fetch opportunities from SAM.gov matching company's NAICS codes.
    
    Strategy: Search NATIONALLY by NAICS code (no state filter).
    The scoring engine handles location filtering — this maximizes
    results per API call and stays within rate limits.
    
    Free API key = ~10 requests/day, so we prioritize the top NAICS
    codes and use large page sizes.
    """
    import asyncio

    api_key = company.get("sam_api_key", "")
    if not api_key:
        return []

    posted_from = (date.today() - timedelta(days=days_back)).strftime("%m/%d/%Y")
    posted_to = date.today().strftime("%m/%d/%Y")
    naics_codes = company.get("naics", [])

    # Prioritize core construction/facilities NAICS codes first
    # (these are most likely to return relevant results)
    priority_prefixes = ["8122", "2362", "2369", "5617", "2382", "5612", "2379", "2389", "2361"]
    priority = []
    other = []
    for n in naics_codes:
        if any(n.startswith(p) for p in priority_prefixes):
            priority.append(n)
        else:
            other.append(n)
    
    # Search priority codes first, then others, max 8 calls total
    search_codes = (priority + other)[:8]

    all_opps = []
    existing_notice_ids = {o.get("sam_notice_id") for o in opportunities if o.get("sam_notice_id")}
    calls_made = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        for naics in search_codes:
            try:
                params = {
                    "api_key": api_key,
                    "limit": 100,  # max results per call
                    "offset": 0,
                    "postedFrom": posted_from,
                    "postedTo": posted_to,
                    "ncode": naics,
                    "ptype": "p,o,k",  # pre-sol, solicitation, combined
                    # NO state filter — search nationally
                }

                # Rate limit: wait 1.5s between calls
                if calls_made > 0:
                    await asyncio.sleep(1.5)

                resp = await client.get(SAM_API_URL, params=params)
                calls_made += 1
                print(f"SAM.gov call #{calls_made}: NAICS {naics} → HTTP {resp.status_code}")

                if resp.status_code == 429:
                    print("SAM.gov rate limit hit — stopping further calls")
                    break
                if resp.status_code != 200:
                    print(f"SAM.gov error: {resp.status_code} — {resp.text[:200]}")
                    continue

                data = resp.json()
                sam_opps = data.get("opportunitiesData", [])
                total = data.get("totalRecords", 0)
                print(f"  → {len(sam_opps)} results (of {total} total)")

                for s in sam_opps:
                    notice_id = s.get("noticeId", "")
                    if notice_id in existing_notice_ids:
                        continue
                    existing_notice_ids.add(notice_id)

                    # Parse set-aside
                    sa_code = s.get("typeOfSetAside") or ""
                    set_aside = SET_ASIDE_MAP.get(sa_code, sa_code or "Full & Open")

                    # Parse location from placeOfPerformance
                    pop = s.get("placeOfPerformance", {}) or {}
                    pop_state = ""
                    if pop:
                        state_obj = pop.get("state", {}) or {}
                        pop_state = state_obj.get("code", "") if isinstance(state_obj, dict) else ""

                    # Parse due date
                    deadline = s.get("responseDeadLine") or ""
                    due_date = ""
                    if deadline:
                        try:
                            dt = datetime.strptime(deadline[:10], "%Y-%m-%d")
                            due_date = dt.strftime("%Y-%m-%d")
                        except:
                            try:
                                dt = datetime.strptime(deadline, "%m/%d/%Y")
                                due_date = dt.strftime("%Y-%m-%d")
                            except:
                                due_date = ""

                    # Skip already-expired opportunities
                    if due_date:
                        try:
                            if datetime.strptime(due_date, "%Y-%m-%d").date() < date.today():
                                continue
                        except:
                            pass

                    # Parse value from award if available
                    award = s.get("award", {}) or {}
                    value = 0
                    if award:
                        try:
                            value = float(award.get("amount", 0) or 0)
                        except:
                            value = 0

                    # Build description from title + department
                    dept = s.get("fullParentPathName", "") or s.get("department", "") or ""
                    description = s.get("description", "") or s.get("title", "") or ""

                    opp = {
                        "id": f"sam-{uuid.uuid4().hex[:8]}",
                        "company_id": company["id"],
                        "title": (s.get("title") or "Untitled").strip(),
                        "agency": dept,
                        "naics": s.get("naicsCode") or naics,
                        "location": pop_state or "",
                        "due_date": due_date,
                        "value": value,
                        "set_aside": set_aside,
                        "scope": description[:2000],
                        "status": "new",
                        "source": "sam.gov",
                        "sam_notice_id": notice_id,
                        "sam_sol_number": s.get("solicitationNumber", ""),
                        "sam_posted_date": s.get("postedDate", ""),
                        "sam_type": s.get("type", ""),
                        "sam_link": f"https://sam.gov/opp/{notice_id}/view" if notice_id else "",
                    }
                    all_opps.append(opp)

            except Exception as e:
                print(f"SAM.gov fetch error for NAICS {naics}: {e}")
                continue

    print(f"SAM.gov fetch complete: {calls_made} API calls, {len(all_opps)} unique opportunities found")
    return all_opps


# ── Proposal Templates ──
def generate_proposal(section: str, opp: dict, company: dict, past: list) -> str:
    templates = {
        "executive": f"""EXECUTIVE SUMMARY — DRAFT

{company['name']} is pleased to submit this proposal for the {opp['title']} project in response to the solicitation issued by {opp.get('agency','the agency')}.

As a certified {', '.join(company.get('certifications',[]))} firm, {company['name']} brings proven experience in {', '.join(company.get('services',[])[: 3])} with a strong track record of delivering government projects on time and within budget.

Our team understands the critical nature of this work and is committed to providing the highest quality results. With bonding capacity of ${company.get('bonding_capacity',0)/1e6:.1f}M and operations across {', '.join(company.get('regions',[]))}, we are well-positioned to execute this scope.

[ADD: Specific approach summary]
[ADD: Key differentiators]
[ADD: Timeline commitment]""",

        "technical": f"""TECHNICAL APPROACH — DRAFT

Project Understanding:
{opp.get('scope','[Scope not provided]')}

Our approach to the {opp['title']} project encompasses the following phases:

Phase 1 — Mobilization & Planning
- Site assessment and conditions survey
- Detailed work plan and schedule development
- Safety plan and quality control plan submission
- Subcontractor coordination and material procurement

Phase 2 — Execution
- [ADD: Specific tasks from scope]
- Quality control inspections at each milestone
- Daily progress reporting and photo documentation
- Coordination with facility operations

Phase 3 — Closeout
- Final inspections and punch list resolution
- As-built documentation and O&M manuals
- Warranty coordination
- Site restoration""",

        "pastPerformance": "PAST PERFORMANCE — DRAFT\n\n" + "\n\n".join([
            f"{i+1}. {p['name']}\n   Client: {p['client']}\n   Value: ${p['value']:,.0f}\n   Year: {p['year']}\n   Scope: {p['scope']}\n   [ADD: Client reference contact]"
            for i, p in enumerate(past)
        ]) if past else "PAST PERFORMANCE — DRAFT\n\n[No past projects loaded yet. Add projects in Company Profile.]",

        "staffing": f"""STAFFING PLAN — DRAFT

Key Personnel:

1. Project Manager — [Name, qualifications, years of experience]
2. Site Superintendent — [Name, qualifications]
3. Safety Officer — [Name, OSHA certifications]
4. Quality Control Manager — [Name, certifications]

[ADD: Organizational chart]
[ADD: Resumes as appendix]""",

        "compliance": "COMPLIANCE CHECKLIST — DRAFT\n\n" +
            "[ ] SAM.gov registration current\n" +
            "[ ] UEI number verified\n" +
            "[ ] CAGE code current\n" +
            "\n".join([f"[ ] {c} certification current" for c in company.get("certifications", [])]) +
            f"\n[ ] NAICS {opp.get('naics','')} confirmed\n" +
            f"[ ] Bonding capacity sufficient (${company.get('bonding_capacity',0)/1e6:.1f}M)\n" +
            "[ ] Insurance certificates current\n" +
            f"[ ] Required licenses for {opp.get('location','')} obtained\n" +
            "[ ] Past performance references prepared\n" +
            "[ ] Safety plan prepared\n" +
            "[ ] Quality control plan prepared\n" +
            "[ ] Wage determination reviewed\n" +
            "[ ] All solicitation amendments acknowledged"
    }
    return templates.get(section, "Section not found.")


# ── Routes ──

@app.get("/")
def root():
    return {"app": "ConstructBid AI", "version": "3.0.0", "status": "running",
            "auto_refresh": auto_refresh_status}

# Company
@app.get("/api/company/{company_id}")
def get_company(company_id: str):
    c = next((c for c in companies if c["id"] == company_id), None)
    if not c:
        raise HTTPException(404, "Company not found")
    # Don't expose full API key
    safe = dict(c)
    key = safe.get("sam_api_key", "")
    safe["sam_api_key_set"] = bool(key)
    safe["sam_api_key_preview"] = key[:6] + "..." if len(key) > 6 else key
    return safe

@app.put("/api/company/{company_id}")
def update_company(company_id: str, data: CompanyUpdate):
    for i, c in enumerate(companies):
        if c["id"] == company_id:
            companies[i].update(data.dict())
            save_json("companies", companies)
            return companies[i]
    raise HTTPException(404, "Company not found")

# Opportunities
@app.get("/api/opportunities/{company_id}")
def list_opportunities(company_id: str):
    opps = [o for o in opportunities if o["company_id"] == company_id]
    company = next((c for c in companies if c["id"] == company_id), companies[0])
    results = []
    for o in opps:
        s = score_opportunity(o, company)
        results.append({**o, **s})
    return sorted(results, key=lambda x: x["score"], reverse=True)

@app.post("/api/opportunities/{company_id}")
def create_opportunity(company_id: str, data: OpportunityCreate):
    opp = {"id": f"opp-{uuid.uuid4().hex[:8]}", "company_id": company_id,
           **data.dict(), "status": "new", "source": "manual", "sam_notice_id": None}
    opportunities.append(opp)
    save_json("opportunities", opportunities)
    company = next((c for c in companies if c["id"] == company_id), companies[0])
    return {**opp, **score_opportunity(opp, company)}

# SAM.gov Fetch
@app.post("/api/sam-fetch/{company_id}")
async def sam_fetch(company_id: str, req: SAMFetchRequest):
    company = next((c for c in companies if c["id"] == company_id), None)
    if not company:
        raise HTTPException(404, "Company not found")
    if not company.get("sam_api_key"):
        raise HTTPException(400, "SAM.gov API key not set. Add it in Company Profile.")

    new_opps = await fetch_sam_opportunities(company, req.days_back)

    # Score and filter
    scored = []
    for o in new_opps:
        s = score_opportunity(o, company)
        if s["score"] >= req.min_score:
            scored.append({**o, **s})

    # Save the good ones
    added = 0
    for o in scored:
        base = {k: v for k, v in o.items() if k not in ("score", "recommendation", "reasons")}
        opportunities.append(base)
        added += 1

    if added > 0:
        save_json("opportunities", opportunities)

    return {
        "fetched": len(new_opps),
        "added": added,
        "min_score_filter": req.min_score,
        "opportunities": sorted(scored, key=lambda x: x["score"], reverse=True)
    }

# Refresh — clears all SAM.gov opps and re-fetches fresh
@app.post("/api/sam-refresh/{company_id}")
async def sam_refresh(company_id: str, req: SAMFetchRequest):
    global opportunities
    company = next((c for c in companies if c["id"] == company_id), None)
    if not company:
        raise HTTPException(404, "Company not found")
    if not company.get("sam_api_key"):
        raise HTTPException(400, "SAM.gov API key not set. Add it in Company Profile.")

    # Remove all old SAM.gov opportunities for this company
    old_count = len([o for o in opportunities if o.get("source") == "sam.gov" and o.get("company_id") == company_id])
    opportunities = [o for o in opportunities if not (o.get("source") == "sam.gov" and o.get("company_id") == company_id)]

    # Fetch fresh
    new_opps = await fetch_sam_opportunities(company, req.days_back)

    # Score, filter, and save
    scored = []
    added = 0
    for o in new_opps:
        s = score_opportunity(o, company)
        if s["score"] >= req.min_score:
            scored.append({**o, **s})
            base = {k: v for k, v in {**o, **s}.items() if k not in ("score", "recommendation", "reasons")}
            opportunities.append(base)
            added += 1

    save_json("opportunities", opportunities)

    return {
        "cleared": old_count,
        "fetched": len(new_opps),
        "added": added,
        "min_score_filter": req.min_score,
        "opportunities": sorted(scored, key=lambda x: x["score"], reverse=True)
    }

# Clear all expired opportunities
@app.post("/api/clear-expired/{company_id}")
def clear_expired(company_id: str):
    global opportunities
    today = date.today()
    before = len(opportunities)
    kept = []
    removed = 0
    for o in opportunities:
        if o.get("company_id") != company_id:
            kept.append(o)
            continue
        due = o.get("due_date", "")
        if due:
            try:
                if datetime.strptime(due, "%Y-%m-%d").date() < today:
                    removed += 1
                    continue
            except:
                pass
        kept.append(o)
    opportunities = kept
    save_json("opportunities", opportunities)
    return {"removed": removed, "remaining": len([o for o in opportunities if o.get("company_id") == company_id])}

# Clear all PASS-scored opportunities
@app.post("/api/clear-passes/{company_id}")
def clear_passes(company_id: str):
    global opportunities
    company = next((c for c in companies if c["id"] == company_id), companies[0])
    kept = []
    removed = 0
    for o in opportunities:
        if o.get("company_id") != company_id:
            kept.append(o)
            continue
        s = score_opportunity(o, company)
        if s["recommendation"] == "PASS" and o.get("source") == "sam.gov":
            removed += 1
            continue
        kept.append(o)
    opportunities = kept
    save_json("opportunities", opportunities)
    return {"removed": removed, "remaining": len([o for o in opportunities if o.get("company_id") == company_id])}

# Delete opportunity
@app.delete("/api/opportunities/{opportunity_id}")
def delete_opportunity(opportunity_id: str):
    global opportunities
    before = len(opportunities)
    opportunities = [o for o in opportunities if o["id"] != opportunity_id]
    if len(opportunities) < before:
        save_json("opportunities", opportunities)
        return {"deleted": True}
    raise HTTPException(404, "Opportunity not found")

# Scoring
@app.get("/api/score/{opportunity_id}")
def score(opportunity_id: str):
    opp = next((o for o in opportunities if o["id"] == opportunity_id), None)
    if not opp:
        raise HTTPException(404, "Opportunity not found")
    company = next((c for c in companies if c["id"] == opp["company_id"]), companies[0])
    return score_opportunity(opp, company)

# Proposals
@app.post("/api/proposal")
def create_proposal(req: ProposalRequest):
    opp = next((o for o in opportunities if o["id"] == req.opportunity_id), None)
    if not opp:
        raise HTTPException(404, "Opportunity not found")
    company = next((c for c in companies if c["id"] == opp["company_id"]), companies[0])
    past = [p for p in projects if p["company_id"] == opp["company_id"]]
    text = generate_proposal(req.section, opp, company, past)
    return {"section": req.section, "content": text}

# Field Reports
@app.post("/api/field-report")
def create_field_report(req: FieldReportRequest):
    today = datetime.now().strftime("%A, %B %d, %Y")
    report = f"""DAILY FIELD REPORT
{'━' * 40}
Date: {today}
Project: {req.project_name}
Prepared by: [Superintendent Name]
Weather: [Enter conditions]

─── WORK PERFORMED TODAY ───
{req.notes}

─── LABOR ON SITE ───
• Company crew: [#] workers
• Subcontractors: [List]
• Total manhours: [#]

─── ISSUES / DELAYS ───
• [Describe any issues]

─── SAFETY ───
• Incidents: None
• Toolbox talk: [Topic]

─── TOMORROW'S PLAN ───
• [Planned activities]
{'━' * 40}"""
    return {"report": report}

# Projects
@app.get("/api/projects/{company_id}")
def list_projects(company_id: str):
    return [p for p in projects if p["company_id"] == company_id]

# Auto-refresh status
@app.get("/api/auto-refresh-status")
def get_auto_refresh_status():
    return auto_refresh_status

# Test notification
@app.post("/api/test-notification/{company_id}")
async def test_notification(company_id: str):
    company = next((c for c in companies if c["id"] == company_id), None)
    if not company:
        raise HTTPException(404, "Company not found")
    if not company.get("notify_enabled"):
        raise HTTPException(400, "Notifications are not enabled. Turn them on in Company Profile.")
    if not company.get("notify_email") and not company.get("notify_phone"):
        raise HTTPException(400, "No email or phone number set. Add one in Company Profile.")

    # Send a test with a fake opportunity
    test_opp = {
        "title": "TEST — This Is a Test Notification",
        "agency": "ConstructBid AI Test",
        "score": 99,
        "recommendation": "PURSUE",
        "value": 2500000,
        "due_date": "2026-05-01",
        "set_aside": "SDVOSB",
        "naics": "236220",
        "location": "VA",
        "sam_link": "https://sam.gov",
    }
    await send_notifications(company, [test_opp])
    targets = []
    if company.get("notify_email"): targets.append(f"email ({company['notify_email']})")
    if company.get("notify_phone"): targets.append(f"SMS ({company['notify_phone']})")
    return {"sent_to": targets}

# Voice input — parse spoken text into company profile fields
class VoiceInput(BaseModel):
    transcript: str

VOICE_PARSE_PROMPT = """You are parsing a spoken company description into structured data for a government contracting platform. The speech-to-text may have errors — use your best judgment to correct them.

Extract these fields from the transcript. Return ONLY valid JSON, no markdown, no explanation:

{
  "name": "Company name (correct likely speech-to-text errors)",
  "services": ["list of services they provide"],
  "certifications": ["list of certifications - look for SDVOSB, VOSB, 8(a), HUBZone, WOSB, OSHA, SBA Mentor-Protégé, EPA Lead-Safe etc. Note: speech recognition often mangles 'SDVOSB' into things like 'STV OSB', 'SD VOSB', 'service disabled veteran owned small business' etc."],
  "regions": ["2-letter US state codes where they operate"],
  "bonding_capacity": 0,
  "naics": ["6-digit NAICS codes if mentioned"]
}

Rules:
- For bonding_capacity: extract the dollar amount as a plain number. "5 million" = 5000000. Ignore numbers that are clearly NOT bonding (like rankings, years, employee counts).
- For certifications: SDVOSB is extremely common in government contracting. Any mention of "veteran owned", "service disabled", "SDVOSB" (even misspelled) should become "SDVOSB".
- For regions: convert state names to 2-letter codes. "Virginia" = "VA".
- For services: normalize to standard construction/facilities terms.
- For name: this is the actual company name, NOT a phrase like "my company" or "the job I work for". Extract the proper name.
- Only include fields you can actually extract. If a field has no data, omit it from the JSON.
- Return ONLY the JSON object, nothing else."""

@app.post("/api/parse-voice-profile")
async def parse_voice_profile(data: VoiceInput):
    """Parse spoken company description using Claude AI, with keyword fallback."""
    import re

    # Try Claude AI first
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")

    # Also check .env file
    if not anthropic_key:
        env_path = os.path.join(DATA_DIR, "..", ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.strip().startswith("ANTHROPIC_API_KEY="):
                        anthropic_key = line.strip().split("=", 1)[1].strip().strip('"').strip("'")

    if anthropic_key:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": anthropic_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-sonnet-4-20250514",
                        "max_tokens": 1000,
                        "messages": [
                            {"role": "user", "content": VOICE_PARSE_PROMPT + "\n\nTranscript:\n" + data.transcript}
                        ]
                    }
                )

                if resp.status_code == 200:
                    resp_data = resp.json()
                    ai_text = ""
                    for block in resp_data.get("content", []):
                        if block.get("type") == "text":
                            ai_text += block["text"]

                    # Clean and parse JSON
                    ai_text = ai_text.strip()
                    if ai_text.startswith("```"):
                        ai_text = re.sub(r'^```(?:json)?\s*', '', ai_text)
                        ai_text = re.sub(r'\s*```$', '', ai_text)

                    parsed = json.loads(ai_text)

                    # Clean up: remove empty fields
                    result = {}
                    if parsed.get("name"): result["name"] = parsed["name"]
                    if parsed.get("services"): result["services"] = parsed["services"]
                    if parsed.get("certifications"): result["certifications"] = parsed["certifications"]
                    if parsed.get("regions"): result["regions"] = parsed["regions"]
                    if parsed.get("bonding_capacity"): result["bonding_capacity"] = int(parsed["bonding_capacity"])
                    if parsed.get("naics"): result["naics"] = parsed["naics"]

                    return {
                        "parsed": result,
                        "transcript": data.transcript,
                        "fields_found": len(result),
                        "method": "ai",
                    }
                else:
                    print(f"Claude API error: {resp.status_code} — {resp.text[:200]}")
        except Exception as e:
            print(f"Claude AI parse error: {e}")

    # ── Fallback: keyword-based parsing ──
    text = data.transcript.lower()
    result = {}

    # Extract company name
    raw = data.transcript.strip()
    for sep in [" is ", " we ", " our ", ". ", ", "]:
        if sep.lower() in raw.lower():
            idx = raw.lower().index(sep.lower())
            candidate = raw[:idx].strip()
            if 2 < len(candidate) < 80:
                result["name"] = candidate
                break

    # Extract services
    service_keywords = {
        "construction": "General Construction", "general contracting": "General Construction",
        "cemetery": "Cemetery Operations", "cemetery operations": "Cemetery Operations",
        "burial": "Cemetery Operations", "facilities maintenance": "Facilities Maintenance",
        "facilities": "Facilities Maintenance", "building maintenance": "Facilities Maintenance",
        "hvac": "HVAC/Plumbing", "plumbing": "HVAC/Plumbing", "heating": "HVAC/Plumbing",
        "air conditioning": "HVAC/Plumbing", "landscaping": "Landscaping",
        "grounds maintenance": "Grounds Maintenance", "mowing": "Grounds Maintenance",
        "site prep": "Site Preparation", "site preparation": "Site Preparation",
        "excavation": "Site Preparation", "grading": "Site Preparation",
        "demolition": "Demolition", "renovation": "Renovations", "renovations": "Renovations",
        "remodel": "Renovations", "design build": "Design-Build", "design-build": "Design-Build",
        "historical restoration": "Historical Restorations", "restoration": "Historical Restorations",
        "roofing": "Roofing", "electrical": "Electrical", "painting": "Painting",
        "paving": "Paving", "concrete": "Concrete", "masonry": "Masonry",
        "development": "Development & Leasing", "leasing": "Development & Leasing",
        "real estate": "Development & Leasing",
    }
    found_services = []
    for kw, svc in service_keywords.items():
        if kw in text and svc not in found_services:
            found_services.append(svc)
    if found_services:
        result["services"] = found_services

    # Extract certifications
    cert_keywords = {
        "sdvosb": "SDVOSB", "service disabled veteran": "SDVOSB", "veteran owned": "SDVOSB",
        "stv osb": "SDVOSB", "sd vosb": "SDVOSB",  # common speech-to-text errors
        "vosb": "VOSB", "8a": "8(a)", "8(a)": "8(a)", "hubzone": "HUBZone",
        "hub zone": "HUBZone", "woman owned": "WOSB", "wosb": "WOSB",
        "osha": "OSHA 30", "mentor protege": "SBA Mentor-Protégé",
        "mentor-protege": "SBA Mentor-Protégé", "epa lead": "EPA Lead-Safe",
    }
    found_certs = []
    for kw, cert in cert_keywords.items():
        if kw in text and cert not in found_certs:
            found_certs.append(cert)
    if found_certs:
        result["certifications"] = found_certs

    # Extract states
    state_names = {
        "virginia": "VA", "maryland": "MD", "washington dc": "DC", "d.c.": "DC",
        "north carolina": "NC", "south carolina": "SC", "west virginia": "WV",
        "ohio": "OH", "michigan": "MI", "indiana": "IN", "oklahoma": "OK",
        "nebraska": "NE", "california": "CA", "tennessee": "TN", "wyoming": "WY",
        "texas": "TX", "florida": "FL", "georgia": "GA", "alabama": "AL",
        "new york": "NY", "pennsylvania": "PA", "illinois": "IL", "kentucky": "KY",
        "missouri": "MO", "colorado": "CO", "arizona": "AZ", "oregon": "OR",
        "washington": "WA", "iowa": "IA", "kansas": "KS", "arkansas": "AR",
        "mississippi": "MS", "louisiana": "LA", "minnesota": "MN", "wisconsin": "WI",
        "connecticut": "CT", "massachusetts": "MA", "new jersey": "NJ",
        "delaware": "DE", "maine": "ME", "new hampshire": "NH", "vermont": "VT",
        "rhode island": "RI", "montana": "MT", "idaho": "ID", "utah": "UT",
        "nevada": "NV", "new mexico": "NM", "north dakota": "ND", "south dakota": "SD",
        "hawaii": "HI", "alaska": "AK",
    }
    found_states = []
    for name, code in state_names.items():
        if name in text and code not in found_states:
            found_states.append(code)
    state_codes = set(state_names.values())
    words = re.findall(r'\b[A-Z]{2}\b', data.transcript)
    for w in words:
        if w in state_codes and w not in found_states:
            found_states.append(w)
    if found_states:
        result["regions"] = found_states

    # Extract bonding capacity
    money_patterns = [
        r'bonding.*?(\d+)\s*(?:million|mil)',
        r'bond.*?(\d+)\s*(?:million|mil)',
        r'\$(\d+)\s*(?:million|mil|m)\s*(?:per|bond|contract)',
        r'(\d+)\s*(?:million|mil)\s*(?:dollar|bond|per\s*contract)',
    ]
    for pat in money_patterns:
        m = re.search(pat, text)
        if m:
            val = float(m.group(1))
            if val < 1000:
                val *= 1_000_000
            result["bonding_capacity"] = int(val)
            break

    # Extract NAICS codes
    naics_found = re.findall(r'\b(\d{6})\b', data.transcript)
    if naics_found:
        result["naics"] = list(set(naics_found))

    return {
        "parsed": result,
        "transcript": data.transcript,
        "fields_found": len(result),
        "method": "keywords",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
