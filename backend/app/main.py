"""
ConstructBid AI — Government Contractor OS v6
Multi-company SaaS with auth, PostgreSQL, SAM.gov, notifications, voice AI, Stripe billing.
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
import json, os, uuid, httpx, asyncio, re, base64, bcrypt, jwt, stripe
from datetime import datetime, date, timedelta
from contextlib import asynccontextmanager

from app.database import init_db, SessionLocal, User, Company, Opportunity, Project

JWT_SECRET = os.environ.get("JWT_SECRET", "constructbid-ai-secret-change-me")
STRIPE_SECRET = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
TRIAL_DAYS = 14

stripe.api_key = STRIPE_SECRET

def company_to_dict(c):
    return {"id":c.id,"name":c.name,"services":c.services or [],"certifications":c.certifications or [],
            "naics":c.naics or [],"bonding_capacity":c.bonding_capacity or 0,"regions":c.regions or [],
            "sam_api_key":c.sam_api_key or "","notify_email":c.notify_email or "",
            "notify_phone":c.notify_phone or "","notify_enabled":c.notify_enabled or False,
            "notify_min_score":c.notify_min_score or 75,
            "plan_status":c.plan_status or "trial",
            "stripe_customer_id":c.stripe_customer_id or "",
            "stripe_subscription_id":c.stripe_subscription_id or "",
            "trial_ends_at":c.trial_ends_at.isoformat() if c.trial_ends_at else None}

def opp_to_dict(o):
    return {"id":o.id,"company_id":o.company_id,"title":o.title or "","agency":o.agency or "",
            "naics":o.naics or "","location":o.location or "","due_date":o.due_date or "",
            "value":o.value or 0,"set_aside":o.set_aside or "","scope":o.scope or "",
            "status":o.status or "new","source":o.source or "manual","sam_notice_id":o.sam_notice_id,
            "sam_sol_number":o.sam_sol_number or "","sam_posted_date":o.sam_posted_date or "",
            "sam_type":o.sam_type or "","sam_link":o.sam_link or ""}

def proj_to_dict(p):
    return {"id":p.id,"company_id":p.company_id,"name":p.name or "","client":p.client or "",
            "value":p.value or 0,"year":p.year or 0,"scope":p.scope or ""}

def load_env_var(name):
    val = os.environ.get(name, "")
    if val: return val
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.strip().startswith(f"{name}="): return line.strip().split("=",1)[1].strip().strip('"').strip("'")
    return ""

# ── Auth ──
def hash_password(p): return bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()
def verify_password(p, h): return bcrypt.checkpw(p.encode(), h.encode())
def create_token(uid, cid): return jwt.encode({"user_id":uid,"company_id":cid,"exp":datetime.utcnow()+timedelta(days=30)}, JWT_SECRET, algorithm="HS256")

def get_current_user(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "): raise HTTPException(401, "Not logged in")
    try:
        p = jwt.decode(auth[7:], JWT_SECRET, algorithms=["HS256"])
        return {"user_id":p["user_id"],"company_id":p["company_id"]}
    except jwt.ExpiredSignatureError: raise HTTPException(401, "Session expired")
    except: raise HTTPException(401, "Invalid token")

def check_subscription(company):
    """Check if company has active subscription or is in trial."""
    status = company.plan_status or "trial"
    if status == "active": return True
    if status == "trial":
        if company.trial_ends_at:
            if datetime.utcnow() < company.trial_ends_at: return True
            # Trial expired
            company.plan_status = "expired"
            return False
        return True  # No trial end set = unlimited trial for now
    return False  # cancelled, expired


# ── Notifications ──
async def send_notifications(company, new_pursue_opps):
    if not company.get("notify_enabled") or not new_pursue_opps: return
    email=company.get("notify_email","");phone=company.get("notify_phone","")
    comp_name=company.get("name","Your Company");count=len(new_pursue_opps)
    subject=f"🔥 {count} New Opportunit{'ies' if count>1 else 'y'} — ConstructBid AI"
    text_lines=[f"ConstructBid AI found {count} new opportunit{'ies' if count>1 else 'y'} for {comp_name}:\n"]
    for opp in new_pursue_opps[:5]:
        val=opp.get('value',0);text_lines.append(f"• [{opp.get('score',0)} pts] {opp.get('title','')}")
        text_lines.append(f"  Value: {'${:.1f}M'.format(val/1e6) if val else 'TBD'} | Due: {opp.get('due_date','TBD')}")
    plain_text="\n".join(text_lines)
    html_rows=""
    for opp in new_pursue_opps[:5]:
        color="#22c55e" if opp.get("recommendation")=="PURSUE" else "#f59e0b"
        val=opp.get("value",0);val_str=f"${val/1e6:.1f}M" if val else "TBD"
        html_rows+=f'<tr style="border-bottom:1px solid #1e2d3d"><td style="padding:12px;text-align:center"><span style="display:inline-block;width:44px;height:44px;border-radius:50%;border:3px solid {color};line-height:38px;text-align:center;font-weight:700;color:{color}">{opp.get("score",0)}</span></td><td style="padding:12px"><strong style="color:#e2e8f0">{opp.get("title","")}</strong><br><span style="color:#64748b;font-size:12px">{opp.get("agency","")} · {val_str} · Due {opp.get("due_date","TBD")}</span></td></tr>'
    html_body=f'<div style="background:#0a0f1a;padding:20px;font-family:Arial"><div style="max-width:600px;margin:0 auto;background:#111827;border-radius:12px;border:1px solid #1e2d3d"><div style="background:linear-gradient(135deg,#059669,#06b6d4);padding:20px;text-align:center"><h1 style="color:white;margin:0;font-size:20px">🔥 {count} New Opportunities</h1><p style="color:rgba(255,255,255,.8);margin:8px 0 0">{comp_name}</p></div><table style="width:100%;border-collapse:collapse">{html_rows}</table></div></div>'
    if email:
        rk=load_env_var("RESEND_API_KEY")
        if rk:
            try:
                async with httpx.AsyncClient(timeout=15) as c:
                    await c.post("https://api.resend.com/emails",headers={"Authorization":f"Bearer {rk}","Content-Type":"application/json"},json={"from":"ConstructBid AI <onboarding@resend.dev>","to":[email],"subject":subject,"html":html_body,"text":plain_text})
            except Exception as e: print(f"[NOTIFY] Email error: {e}")
    if phone:
        sid=load_env_var("TWILIO_ACCOUNT_SID");tok=load_env_var("TWILIO_AUTH_TOKEN");frm=load_env_var("TWILIO_FROM_NUMBER")
        if sid and tok and frm:
            sms=f"ConstructBid AI: {count} new opportunit{'ies' if count>1 else 'y'}! Top: {new_pursue_opps[0].get('title','')[:60]} ({new_pursue_opps[0].get('score',0)} pts)"
            try:
                auth=base64.b64encode(f"{sid}:{tok}".encode()).decode()
                async with httpx.AsyncClient(timeout=15) as c:
                    await c.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",headers={"Authorization":f"Basic {auth}"},data={"To":phone,"From":frm,"Body":sms})
            except Exception as e: print(f"[NOTIFY] SMS error: {e}")


# ── Scoring ──
SCOPE_KW=["cemetery","columbarium","gravesite","burial","headstone","niche","memorial","interment","construction","renovation","remodel","expansion","demolition","design-build","design build","facilities","maintenance","grounds","landscaping","mowing","irrigation","hvac","plumbing","mechanical","electrical","roofing","painting","concrete","masonry","site prep","excavation","grading","paving","drainage","fencing","restoration","historic","rehabilitation","lease","leasing","repair","replace","install","upgrade","improve"]

def score_opportunity(opp,company):
    score=0;reasons=[];flags=[]
    cn=company.get("naics",[]);cc=[c.upper() for c in company.get("certifications",[])];cr=company.get("regions",[])
    cs=[s.lower() for s in company.get("services",[])];cb=company.get("bonding_capacity",0)
    on=opp.get("naics","") or "";osa=opp.get("set_aside","") or "";ol=opp.get("location","") or ""
    ov=opp.get("value",0) or 0;od=opp.get("due_date","") or ""
    ot=((opp.get("title","") or "")+" "+(opp.get("scope","") or "")).lower()
    if on in cn: score+=35;reasons.append(f"✓ NAICS {on} — exact match")
    elif on[:4] and any(n[:4]==on[:4] for n in cn): score+=25;reasons.append(f"◐ NAICS {on} — related")
    elif on[:3] and any(n[:3]==on[:3] for n in cn): score+=15;reasons.append(f"○ NAICS {on} — same group")
    else: reasons.append(f"✗ NAICS {on} — outside capabilities")
    sa=osa.upper().strip();hsd=any("SDVOSB" in c for c in cc)
    if not sa or sa in("FULL & OPEN","FULL AND OPEN","NONE","N/A"): score+=15;reasons.append("○ Full & open")
    elif "SDVOSB" in sa:
        if hsd: score+=25;reasons.append("✓ SDVOSB matches")
        else: reasons.append("✗ SDVOSB required");flags.append("disq")
    elif "SMALL" in sa or "SBA" in sa: score+=20;reasons.append("✓ Small business")
    elif "8(A)" in sa or "8A" in sa:
        if any("8(A)" in c or "8A" in c for c in cc): score+=25
        else: reasons.append("✗ 8(a) required");flags.append("disq")
    elif "HUBZONE" in sa:
        if any("HUBZONE" in c for c in cc): score+=25
        else: reasons.append("✗ HUBZone required");flags.append("disq")
    elif "VOSB" in sa:
        if hsd: score+=25;reasons.append("✓ VOSB — SDVOSB qualifies")
        else: flags.append("disq")
    else: score+=10;reasons.append(f"? Set-aside '{osa}'")
    dk={w.lower() for s in cs for w in s.split() if len(w)>3}
    ak=set(SCOPE_KW)|dk;mk=[k for k in ak if k in ot]
    hs=[k for k in["cemetery","columbarium","gravesite","burial","memorial"] if k in ot]
    if hs: kw=min(20,12+len(mk)*2);reasons.append(f"✓ Strong: {', '.join(hs[:3])}")
    elif len(mk)>=4: kw=min(20,len(mk)*3);reasons.append(f"✓ Good: {', '.join(list(mk)[:5])}")
    elif len(mk)>=2: kw=min(14,len(mk)*4);reasons.append(f"◐ Partial: {', '.join(list(mk)[:4])}")
    elif len(mk)==1: kw=5;reasons.append(f"○ Weak: {mk[0]}")
    elif on in cn: kw=8;reasons.append("○ No scope — NAICS relevant")
    else: kw=0;reasons.append("✗ No keywords match")
    score+=kw
    adj={"VA":["MD","DC","WV","NC","TN"],"MD":["VA","DC","WV","PA"],"DC":["VA","MD"],"OH":["MI","IN","KY","WV","PA"],"MI":["OH","IN","WI"],"IN":["MI","OH","IL","KY"],"OK":["TX","KS","AR"],"NE":["KS","SD","IA","CO","WY"],"CA":["OR","NV","AZ"],"TN":["VA","NC","GA","AL","KY"]}
    if not ol: score+=6
    elif ol in cr: score+=10;reasons.append(f"✓ In {ol}")
    elif any(ol in adj.get(r,[]) for r in cr): score+=5;reasons.append(f"◐ {ol} — adjacent")
    else: score+=1;reasons.append(f"△ {ol} — outside")
    if ov<=0: score+=4
    elif ov<=cb*.5: score+=5
    elif ov<=cb: score+=4
    elif ov<=cb*1.5: score+=2
    else: reasons.append(f"✗ ${ov/1e6:.1f}M exceeds bonding")
    try:
        if od:
            dl=(datetime.strptime(od,"%Y-%m-%d").date()-date.today()).days
            if dl<0: flags.append("expired")
            elif dl>30: score+=5
            elif dl>14: score+=3
            elif dl>3: score+=1
        else: score+=3
    except: score+=3
    score=min(100,score)
    if "disq" in flags: score=min(score,35);reasons.insert(0,"⚠ DISQUALIFIED")
    if "expired" in flags: score=min(score,25);reasons.insert(0,"⚠ EXPIRED")
    rec="PASS" if("disq" in flags or "expired" in flags) else "PURSUE" if score>=75 else "REVIEW" if score>=55 else "PASS"
    return {"score":score,"recommendation":rec,"reasons":reasons}


# ── SAM.gov ──
SAM_URL="https://api.sam.gov/prod/opportunities/v2/search"
SA_MAP={"SBA":"Small Business","SBP":"Small Business","8A":"8(a)","8AN":"8(a)","HZC":"HUBZone","HZS":"HUBZone","SDVOSBC":"SDVOSB","SDVOSBS":"SDVOSB","VOSBC":"VOSB","VOSBS":"VOSB","":"Full & Open",None:"Full & Open"}

async def fetch_sam(company,days=30):
    ak=company.get("sam_api_key","")
    if not ak: return []
    pf=(date.today()-timedelta(days=days)).strftime("%m/%d/%Y");pt=date.today().strftime("%m/%d/%Y")
    nc=company.get("naics",[]);pp=["8122","2362","5617","2382","5612","2379","2389","2361"]
    pri=[n for n in nc if any(n.startswith(p) for p in pp)];oth=[n for n in nc if n not in pri]
    codes=(pri+oth)[:8];opps=[]
    session=SessionLocal()
    try: eids={o.sam_notice_id for o in session.query(Opportunity.sam_notice_id).filter(Opportunity.sam_notice_id.isnot(None),Opportunity.company_id==company["id"]).all()}
    finally: session.close()
    calls=0
    async with httpx.AsyncClient(timeout=30) as cl:
        for naics in codes:
            try:
                if calls>0: await asyncio.sleep(1.5)
                r=await cl.get(SAM_URL,params={"api_key":ak,"limit":100,"offset":0,"postedFrom":pf,"postedTo":pt,"ncode":naics,"ptype":"p,o,k"})
                calls+=1
                if r.status_code==429: break
                if r.status_code!=200: continue
                for s in r.json().get("opportunitiesData",[]):
                    nid=s.get("noticeId","")
                    if nid in eids: continue
                    eids.add(nid)
                    sa=SA_MAP.get(s.get("typeOfSetAside") or "",s.get("typeOfSetAside") or "Full & Open")
                    pop=s.get("placeOfPerformance",{}) or {};ps=""
                    if pop:
                        so=pop.get("state",{}) or {};ps=so.get("code","") if isinstance(so,dict) else ""
                    dl=s.get("responseDeadLine") or "";dd=""
                    if dl:
                        for f in["%Y-%m-%d","%m/%d/%Y"]:
                            try: dd=datetime.strptime(dl[:10],f).strftime("%Y-%m-%d");break
                            except: pass
                    if dd:
                        try:
                            if datetime.strptime(dd,"%Y-%m-%d").date()<date.today(): continue
                        except: pass
                    aw=s.get("award",{}) or {}
                    try: v=float(aw.get("amount",0) or 0)
                    except: v=0
                    opps.append({"id":f"sam-{uuid.uuid4().hex[:8]}","company_id":company["id"],"title":(s.get("title") or "Untitled").strip(),"agency":s.get("fullParentPathName","") or "","naics":s.get("naicsCode") or naics,"location":ps or "","due_date":dd,"value":v,"set_aside":sa,"scope":(s.get("description","") or s.get("title","") or "")[:2000],"status":"new","source":"sam.gov","sam_notice_id":nid,"sam_sol_number":s.get("solicitationNumber",""),"sam_posted_date":s.get("postedDate",""),"sam_type":s.get("type",""),"sam_link":f"https://sam.gov/opp/{nid}/view" if nid else ""})
            except Exception as e: print(f"SAM error: {e}")
    return opps


# ── Auto-refresh ──
AUTO_REFRESH_HOURS=6
auto_refresh_status={"last_run":None,"next_run":None,"last_result":None,"running":False}

async def auto_refresh_loop():
    while True:
        await asyncio.sleep(10)
        session=SessionLocal()
        try:
            for comp in session.query(Company).all():
                cd=company_to_dict(comp)
                if not cd.get("sam_api_key"): continue
                if not check_subscription(comp): continue
                auto_refresh_status["running"]=True;auto_refresh_status["last_run"]=datetime.now().isoformat()
                try:
                    new=await fetch_sam(cd,30);added=0;notify=[];nm=cd.get("notify_min_score",75)
                    for o in new:
                        s=score_opportunity(o,cd)
                        if s["score"]>=40: session.add(Opportunity(**o));added+=1
                        if s["score"]>=nm: notify.append({**o,**s})
                    session.commit()
                    if notify: await send_notifications(cd,notify)
                    auto_refresh_status["last_result"]=f"Added {added} for {cd['name']}"
                except Exception as e: session.rollback();auto_refresh_status["last_result"]=str(e)[:100]
                auto_refresh_status["running"]=False
        finally: session.close()
        auto_refresh_status["next_run"]=(datetime.now()+timedelta(hours=AUTO_REFRESH_HOURS)).isoformat()
        await asyncio.sleep(AUTO_REFRESH_HOURS*3600)

@asynccontextmanager
async def lifespan(app):
    init_db();task=asyncio.create_task(auto_refresh_loop())
    yield;task.cancel()

app=FastAPI(title="ConstructBid AI",version="6.0.0",lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_methods=["*"],allow_headers=["*"])


# ── Models ──
class SignupRequest(BaseModel):
    email:str;password:str;company_name:str;name:Optional[str]=""
class LoginRequest(BaseModel):
    email:str;password:str
class CompanyUpdate(BaseModel):
    name:str;services:list[str];certifications:list[str];naics:list[str]
    bonding_capacity:float;regions:list[str];sam_api_key:Optional[str]=""
    notify_email:Optional[str]="";notify_phone:Optional[str]=""
    notify_enabled:Optional[bool]=False;notify_min_score:Optional[int]=75
class OpportunityCreate(BaseModel):
    title:str;agency:str;naics:str;location:str;due_date:str;value:float;set_aside:str;scope:str
class FieldReportRequest(BaseModel):
    project_name:str;notes:str
class ProposalRequest(BaseModel):
    section:str;opportunity_id:str
class SAMFetchRequest(BaseModel):
    days_back:Optional[int]=30;min_score:Optional[int]=40
class VoiceInput(BaseModel):
    transcript:str


# ── Proposal Templates ──
def gen_proposal(section,opp,company,past):
    t={"executive":f"EXECUTIVE SUMMARY — DRAFT\n\n{company['name']} is pleased to submit this proposal for {opp['title']} in response to {opp.get('agency','')}.\n\nAs a certified {', '.join(company.get('certifications',[]))} firm, {company['name']} brings proven experience in {', '.join(company.get('services','')[:3])}.\n\nWith bonding of ${company.get('bonding_capacity',0)/1e6:.1f}M across {', '.join(company.get('regions',[]))}, we are well-positioned.\n\n[ADD: Approach]\n[ADD: Differentiators]\n[ADD: Timeline]",
    "technical":f"TECHNICAL APPROACH — DRAFT\n\nProject Understanding:\n{opp.get('scope','[Not provided]')}\n\nPhase 1 — Mobilization\n- Site assessment\n- Work plan\n- Safety/QC plans\n\nPhase 2 — Execution\n- [ADD: Tasks]\n- QC inspections\n- Daily reporting\n\nPhase 3 — Closeout\n- Final inspections\n- As-built docs\n- Site restoration",
    "pastPerformance":"PAST PERFORMANCE — DRAFT\n\n"+("\n\n".join([f"{i+1}. {p['name']}\n   Client: {p['client']}\n   Value: ${p['value']:,.0f}\n   Year: {p['year']}\n   Scope: {p['scope']}" for i,p in enumerate(past)]) if past else "[No projects yet]"),
    "staffing":"STAFFING PLAN — DRAFT\n\n1. Project Manager — [Name]\n2. Superintendent — [Name]\n3. Safety Officer — [Certs]\n4. QC Manager — [Certs]",
    "compliance":"COMPLIANCE CHECKLIST — DRAFT\n\n"+"\n".join([f"[ ] {c} current" for c in company.get("certifications",[])])+f"\n[ ] NAICS {opp.get('naics','')} confirmed\n[ ] Bonding sufficient\n[ ] Insurance current\n[ ] Licenses obtained"}
    return t.get(section,"Section not found.")


# ═══ ROUTES ═══

@app.get("/",response_class=HTMLResponse)
def landing():
    for p in [os.path.join(os.path.dirname(__file__),"..","..","landing.html"),
              os.path.join(os.path.dirname(__file__),"..","landing.html")]:
        if os.path.exists(p):
            with open(p) as f: return HTMLResponse(f.read())
    return HTMLResponse('<meta http-equiv="refresh" content="0;url=/dashboard">')

@app.get("/api/status")
def status():
    return {"app":"ConstructBid AI","version":"6.0.0","status":"running"}

@app.get("/dashboard",response_class=HTMLResponse)
def dashboard():
    for p in [os.path.join(os.path.dirname(__file__),"..","..","constructbid-ai-dashboard.html"),
              os.path.join(os.path.dirname(__file__),"..","constructbid-ai-dashboard.html")]:
        if os.path.exists(p):
            with open(p) as f: return HTMLResponse(f.read())
    return HTMLResponse("<h1>Dashboard not found</h1>",status_code=404)

# ── Auth ──
@app.post("/api/signup")
def signup(data:SignupRequest):
    if len(data.password)<6: raise HTTPException(400,"Password must be 6+ characters")
    session=SessionLocal()
    try:
        if session.query(User).filter(User.email==data.email.lower().strip()).first():
            raise HTTPException(400,"Email already registered")
        cid=f"co-{uuid.uuid4().hex[:8]}";uid=f"usr-{uuid.uuid4().hex[:8]}"
        trial_end = datetime.utcnow() + timedelta(days=TRIAL_DAYS)
        company=Company(id=cid,name=data.company_name or "My Company",plan_status="trial",trial_ends_at=trial_end)
        user=User(id=uid,email=data.email.lower().strip(),password_hash=hash_password(data.password),name=data.name or "",company_id=cid)
        session.add(company);session.add(user);session.commit()
        token=create_token(uid,cid)
        return {"token":token,"user":{"id":uid,"email":user.email,"name":user.name,"company_id":cid},
                "company":{"id":cid,"name":company.name,"plan_status":"trial","trial_days_left":TRIAL_DAYS}}
    finally: session.close()

@app.post("/api/login")
def login(data:LoginRequest):
    session=SessionLocal()
    try:
        user=session.query(User).filter(User.email==data.email.lower().strip()).first()
        if not user or not verify_password(data.password,user.password_hash):
            raise HTTPException(401,"Invalid email or password")
        token=create_token(user.id,user.company_id)
        comp=session.query(Company).filter(Company.id==user.company_id).first()
        plan_info = get_plan_info(comp)
        return {"token":token,"user":{"id":user.id,"email":user.email,"name":user.name,"company_id":user.company_id},
                "company":{"id":comp.id,"name":comp.name,**plan_info} if comp else None}
    finally: session.close()

@app.get("/api/me")
def get_me(request:Request):
    u=get_current_user(request)
    session=SessionLocal()
    try:
        user=session.query(User).filter(User.id==u["user_id"]).first()
        comp=session.query(Company).filter(Company.id==u["company_id"]).first()
        if not user: raise HTTPException(404)
        plan_info = get_plan_info(comp) if comp else {}
        return {"user":{"id":user.id,"email":user.email,"name":user.name,"company_id":user.company_id},
                "company":{"id":comp.id,"name":comp.name,**plan_info} if comp else None}
    finally: session.close()

def get_plan_info(comp):
    """Get plan status and trial info."""
    if not comp: return {"plan_status":"trial"}
    status = comp.plan_status or "trial"
    info = {"plan_status": status}
    if status == "trial" and comp.trial_ends_at:
        days_left = (comp.trial_ends_at - datetime.utcnow()).days
        if days_left < 0:
            info["plan_status"] = "expired"
            info["trial_days_left"] = 0
        else:
            info["trial_days_left"] = days_left
    elif status == "active":
        info["trial_days_left"] = None
    return info


# ── Stripe Billing ──
@app.post("/api/create-checkout")
def create_checkout(request:Request):
    u=get_current_user(request)
    if not STRIPE_SECRET or not STRIPE_PRICE_ID:
        raise HTTPException(500, "Stripe not configured")
    session=SessionLocal()
    try:
        comp=session.query(Company).filter(Company.id==u["company_id"]).first()
        user=session.query(User).filter(User.id==u["user_id"]).first()
        if not comp: raise HTTPException(404)

        # Create or reuse Stripe customer
        if comp.stripe_customer_id:
            customer_id = comp.stripe_customer_id
        else:
            customer = stripe.Customer.create(
                email=user.email,
                name=comp.name,
                metadata={"company_id": comp.id, "user_id": user.id}
            )
            comp.stripe_customer_id = customer.id
            session.commit()
            customer_id = customer.id

        # Determine base URL
        base_url = str(request.base_url).rstrip("/")

        checkout = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            mode="subscription",
            success_url=f"{base_url}/dashboard?billing=success",
            cancel_url=f"{base_url}/dashboard?billing=cancelled",
            metadata={"company_id": comp.id}
        )
        return {"checkout_url": checkout.url}
    finally: session.close()

@app.post("/api/billing-portal")
def billing_portal(request:Request):
    u=get_current_user(request)
    session=SessionLocal()
    try:
        comp=session.query(Company).filter(Company.id==u["company_id"]).first()
        if not comp or not comp.stripe_customer_id:
            raise HTTPException(400, "No billing account found. Subscribe first.")
        base_url = str(request.base_url).rstrip("/")
        portal = stripe.billing_portal.Session.create(
            customer=comp.stripe_customer_id,
            return_url=f"{base_url}/dashboard"
        )
        return {"portal_url": portal.url}
    finally: session.close()

@app.get("/api/billing-status")
def billing_status(request:Request):
    u=get_current_user(request)
    session=SessionLocal()
    try:
        comp=session.query(Company).filter(Company.id==u["company_id"]).first()
        if not comp: raise HTTPException(404)
        return get_plan_info(comp)
    finally: session.close()

@app.post("/api/stripe-webhook")
async def stripe_webhook(request:Request):
    """Handle Stripe webhook events."""
    body = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        if STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(body, sig, STRIPE_WEBHOOK_SECRET)
        else:
            event = json.loads(body)
    except Exception as e:
        print(f"[STRIPE] Webhook error: {e}")
        raise HTTPException(400, "Invalid webhook")

    event_type = event.get("type", "") if isinstance(event, dict) else event.type
    data = event.get("data", {}).get("object", {}) if isinstance(event, dict) else event.data.object

    session = SessionLocal()
    try:
        if event_type == "checkout.session.completed":
            cid = data.get("metadata", {}).get("company_id", "")
            sub_id = data.get("subscription", "")
            customer_id = data.get("customer", "")
            if cid:
                comp = session.query(Company).filter(Company.id == cid).first()
                if comp:
                    comp.plan_status = "active"
                    comp.stripe_subscription_id = sub_id
                    comp.stripe_customer_id = customer_id
                    session.commit()
                    print(f"[STRIPE] ✓ {comp.name} subscribed!")

        elif event_type in ("customer.subscription.deleted", "customer.subscription.paused"):
            sub_id = data.get("id", "")
            if sub_id:
                comp = session.query(Company).filter(Company.stripe_subscription_id == sub_id).first()
                if comp:
                    comp.plan_status = "cancelled"
                    session.commit()
                    print(f"[STRIPE] ✗ {comp.name} cancelled")

        elif event_type == "customer.subscription.updated":
            sub_id = data.get("id", "")
            status = data.get("status", "")
            if sub_id:
                comp = session.query(Company).filter(Company.stripe_subscription_id == sub_id).first()
                if comp:
                    if status == "active":
                        comp.plan_status = "active"
                    elif status in ("past_due", "unpaid"):
                        comp.plan_status = "expired"
                    session.commit()

        elif event_type == "invoice.payment_failed":
            customer_id = data.get("customer", "")
            if customer_id:
                comp = session.query(Company).filter(Company.stripe_customer_id == customer_id).first()
                if comp:
                    comp.plan_status = "expired"
                    session.commit()
                    print(f"[STRIPE] ! Payment failed for {comp.name}")
    finally:
        session.close()

    return {"received": True}


# ── Company ──
@app.get("/api/company")
def get_company(request:Request):
    u=get_current_user(request)
    session=SessionLocal()
    try:
        c=session.query(Company).filter(Company.id==u["company_id"]).first()
        if not c: raise HTTPException(404)
        d=company_to_dict(c);k=d.get("sam_api_key","")
        d["sam_api_key_set"]=bool(k);d["sam_api_key_preview"]=k[:6]+"..." if len(k)>6 else k
        d.update(get_plan_info(c))
        return d
    finally: session.close()

@app.put("/api/company")
def update_company(data:CompanyUpdate,request:Request):
    u=get_current_user(request)
    session=SessionLocal()
    try:
        c=session.query(Company).filter(Company.id==u["company_id"]).first()
        if not c: raise HTTPException(404)
        for k,v in data.dict().items(): setattr(c,k,v)
        c.updated_at=datetime.utcnow();session.commit()
        return company_to_dict(c)
    finally: session.close()

# ── Opportunities ──
@app.get("/api/opportunities")
def list_opportunities(request:Request):
    u=get_current_user(request)
    session=SessionLocal()
    try:
        c=session.query(Company).filter(Company.id==u["company_id"]).first()
        cd=company_to_dict(c) if c else {}
        opps=session.query(Opportunity).filter(Opportunity.company_id==u["company_id"]).all()
        return sorted([{**opp_to_dict(o),**score_opportunity(opp_to_dict(o),cd)} for o in opps],key=lambda x:x["score"],reverse=True)
    finally: session.close()

@app.post("/api/opportunities")
def create_opportunity(data:OpportunityCreate,request:Request):
    u=get_current_user(request)
    session=SessionLocal()
    try:
        opp=Opportunity(id=f"opp-{uuid.uuid4().hex[:8]}",company_id=u["company_id"],**data.dict(),source="manual")
        session.add(opp);session.commit()
        c=session.query(Company).filter(Company.id==u["company_id"]).first()
        return {**opp_to_dict(opp),**score_opportunity(opp_to_dict(opp),company_to_dict(c) if c else {})}
    finally: session.close()

@app.delete("/api/opportunities/{opp_id}")
def delete_opportunity(opp_id:str,request:Request):
    u=get_current_user(request)
    session=SessionLocal()
    try:
        o=session.query(Opportunity).filter(Opportunity.id==opp_id,Opportunity.company_id==u["company_id"]).first()
        if not o: raise HTTPException(404)
        session.delete(o);session.commit();return {"deleted":True}
    finally: session.close()

@app.post("/api/sam-refresh")
async def sam_refresh(req:SAMFetchRequest,request:Request):
    u=get_current_user(request)
    session=SessionLocal()
    try:
        c=session.query(Company).filter(Company.id==u["company_id"]).first()
        if not c: raise HTTPException(404)
        cd=company_to_dict(c)
        if not cd.get("sam_api_key"): raise HTTPException(400,"SAM.gov API key not set.")
        old=session.query(Opportunity).filter(Opportunity.source=="sam.gov",Opportunity.company_id==u["company_id"]).count()
        session.query(Opportunity).filter(Opportunity.source=="sam.gov",Opportunity.company_id==u["company_id"]).delete()
        session.commit()
    finally: session.close()
    new=await fetch_sam(cd,req.days_back);scored=[];added=0
    session=SessionLocal()
    try:
        for o in new:
            s=score_opportunity(o,cd)
            if s["score"]>=req.min_score: scored.append({**o,**s});session.add(Opportunity(**o));added+=1
        session.commit()
    finally: session.close()
    return {"cleared":old,"fetched":len(new),"added":added,"opportunities":sorted(scored,key=lambda x:x["score"],reverse=True)}

@app.post("/api/clear-expired")
def clear_expired(request:Request):
    u=get_current_user(request)
    session=SessionLocal()
    try:
        ts=date.today().strftime("%Y-%m-%d")
        opps=session.query(Opportunity).filter(Opportunity.company_id==u["company_id"],Opportunity.due_date<ts,Opportunity.due_date!="").all()
        rm=len(opps)
        for o in opps: session.delete(o)
        session.commit();return {"removed":rm,"remaining":session.query(Opportunity).filter(Opportunity.company_id==u["company_id"]).count()}
    finally: session.close()

@app.post("/api/clear-passes")
def clear_passes(request:Request):
    u=get_current_user(request)
    session=SessionLocal()
    try:
        c=session.query(Company).filter(Company.id==u["company_id"]).first();cd=company_to_dict(c) if c else {}
        opps=session.query(Opportunity).filter(Opportunity.company_id==u["company_id"],Opportunity.source=="sam.gov").all()
        rm=0
        for o in opps:
            if score_opportunity(opp_to_dict(o),cd)["recommendation"]=="PASS": session.delete(o);rm+=1
        session.commit();return {"removed":rm,"remaining":session.query(Opportunity).filter(Opportunity.company_id==u["company_id"]).count()}
    finally: session.close()

@app.post("/api/proposal")
def create_proposal(req:ProposalRequest,request:Request):
    u=get_current_user(request)
    session=SessionLocal()
    try:
        o=session.query(Opportunity).filter(Opportunity.id==req.opportunity_id,Opportunity.company_id==u["company_id"]).first()
        if not o: raise HTTPException(404)
        c=session.query(Company).filter(Company.id==u["company_id"]).first()
        ps=[proj_to_dict(p) for p in session.query(Project).filter(Project.company_id==u["company_id"]).all()]
        return {"section":req.section,"content":gen_proposal(req.section,opp_to_dict(o),company_to_dict(c) if c else {},ps)}
    finally: session.close()

@app.post("/api/field-report")
def create_field_report(req:FieldReportRequest,request:Request):
    get_current_user(request)
    today=datetime.now().strftime("%A, %B %d, %Y")
    return {"report":f"DAILY FIELD REPORT\n{'━'*40}\nDate: {today}\nProject: {req.project_name}\nPrepared by: [Name]\nWeather: [Conditions]\n\n─── WORK PERFORMED ───\n{req.notes}\n\n─── LABOR ───\n• Crew: [#]\n• Subs: [List]\n\n─── ISSUES ───\n• [Describe]\n\n─── SAFETY ───\n• Incidents: None\n\n─── TOMORROW ───\n• [Plan]\n{'━'*40}"}

@app.get("/api/projects")
def list_projects(request:Request):
    u=get_current_user(request)
    session=SessionLocal()
    try: return [proj_to_dict(p) for p in session.query(Project).filter(Project.company_id==u["company_id"]).all()]
    finally: session.close()

@app.get("/api/auto-refresh-status")
def get_auto_refresh_status(): return auto_refresh_status

@app.post("/api/test-notification")
async def test_notification(request:Request):
    u=get_current_user(request)
    session=SessionLocal()
    try:
        c=session.query(Company).filter(Company.id==u["company_id"]).first()
        if not c: raise HTTPException(404)
        cd=company_to_dict(c)
        if not cd.get("notify_enabled"): raise HTTPException(400,"Notifications not enabled")
    finally: session.close()
    await send_notifications(cd,[{"title":"TEST Notification","agency":"ConstructBid AI","score":99,"recommendation":"PURSUE","value":2500000,"due_date":"2026-05-01","set_aside":"SDVOSB","naics":"236220","location":"VA","sam_link":"https://sam.gov"}])
    t=[]
    if cd.get("notify_email"): t.append(f"email ({cd['notify_email']})")
    if cd.get("notify_phone"): t.append(f"SMS ({cd['notify_phone']})")
    return {"sent_to":t}

# ── Voice AI ──
VP="""Parse spoken company description into JSON. Fix speech-to-text errors. Return ONLY JSON:
{"name":"","services":[],"certifications":[],"regions":[],"bonding_capacity":0,"naics":[]}
SDVOSB often heard as "STV OSB". States→2-letter codes. Bonding in dollars. Only include fields with data."""

@app.post("/api/parse-voice-profile")
async def parse_voice_profile(data:VoiceInput,request:Request):
    get_current_user(request)
    ak=load_env_var("ANTHROPIC_API_KEY")
    if ak:
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r=await c.post("https://api.anthropic.com/v1/messages",headers={"x-api-key":ak,"anthropic-version":"2023-06-01","content-type":"application/json"},json={"model":"claude-sonnet-4-20250514","max_tokens":1000,"messages":[{"role":"user","content":VP+"\n\nTranscript:\n"+data.transcript}]})
                if r.status_code==200:
                    txt="".join(b["text"] for b in r.json().get("content",[]) if b.get("type")=="text")
                    txt=re.sub(r'^```(?:json)?\s*','',txt.strip());txt=re.sub(r'\s*```$','',txt)
                    p=json.loads(txt);result={k:v for k,v in p.items() if v}
                    if "bonding_capacity" in result: result["bonding_capacity"]=int(result["bonding_capacity"])
                    return {"parsed":result,"transcript":data.transcript,"fields_found":len(result),"method":"ai"}
        except Exception as e: print(f"Voice AI error: {e}")
    text=data.transcript.lower();result={}
    svc={"construction":"General Construction","cemetery":"Cemetery Operations","facilities":"Facilities Maintenance","hvac":"HVAC/Plumbing","landscaping":"Landscaping","demolition":"Demolition","renovation":"Renovations"}
    result["services"]=list({v for k,v in svc.items() if k in text}) or None
    cert={"sdvosb":"SDVOSB","stv osb":"SDVOSB","veteran":"SDVOSB","8a":"8(a)","hubzone":"HUBZone"}
    result["certifications"]=list({v for k,v in cert.items() if k in text}) or None
    st={"virginia":"VA","ohio":"OH","michigan":"MI","california":"CA","florida":"FL","georgia":"GA","texas":"TX","maine":"ME","maryland":"MD","north carolina":"NC","tennessee":"TN","oklahoma":"OK","nebraska":"NE","wyoming":"WY","indiana":"IN"}
    result["regions"]=[v for k,v in st.items() if k in text] or None
    result={k:v for k,v in result.items() if v}
    return {"parsed":result,"transcript":data.transcript,"fields_found":len(result),"method":"keywords"}

if __name__=="__main__":
    import uvicorn;uvicorn.run(app,host="0.0.0.0",port=8000)
