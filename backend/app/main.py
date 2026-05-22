"""
ConstructBid AI v8 — Full SaaS with AI chat, onboarding, weekly digest.
"""
from fastapi import FastAPI,HTTPException,Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
import json,os,uuid,httpx,asyncio,re,base64,bcrypt,jwt,stripe
from datetime import datetime,date,timedelta
from contextlib import asynccontextmanager
from app.database import init_db,SessionLocal,User,Company,Opportunity,Project

JWT_SECRET=os.environ.get("JWT_SECRET","constructbid-secret")
STRIPE_SECRET=os.environ.get("STRIPE_SECRET_KEY","")
STRIPE_PRICE_ID=os.environ.get("STRIPE_PRICE_ID","")
STRIPE_WEBHOOK_SECRET=os.environ.get("STRIPE_WEBHOOK_SECRET","")
TRIAL_DAYS=14
stripe.api_key=STRIPE_SECRET

def c2d(c):
    return{"id":c.id,"name":c.name,"services":c.services or[],"certifications":c.certifications or[],
    "naics":c.naics or[],"bonding_capacity":c.bonding_capacity or 0,"regions":c.regions or[],
    "sam_api_key":c.sam_api_key or"","notify_email":c.notify_email or"","notify_phone":c.notify_phone or"",
    "notify_enabled":c.notify_enabled or False,"notify_min_score":c.notify_min_score or 75,
    "plan_status":c.plan_status or"trial","stripe_customer_id":c.stripe_customer_id or"",
    "stripe_subscription_id":c.stripe_subscription_id or"","theme":c.theme or"dark-blue",
    "trial_ends_at":c.trial_ends_at.isoformat() if c.trial_ends_at else None}
def o2d(o):
    return{"id":o.id,"company_id":o.company_id,"title":o.title or"","agency":o.agency or"",
    "naics":o.naics or"","location":o.location or"","due_date":o.due_date or"","value":o.value or 0,
    "set_aside":o.set_aside or"","scope":o.scope or"","status":o.status or"new","source":o.source or"manual",
    "notes":o.notes or"","outcome":o.outcome or"","outcome_value":o.outcome_value or 0,
    "sam_notice_id":o.sam_notice_id,"sam_sol_number":o.sam_sol_number or"",
    "sam_posted_date":o.sam_posted_date or"","sam_type":o.sam_type or"","sam_link":o.sam_link or""}
def p2d(p):
    return{"id":p.id,"company_id":p.company_id,"name":p.name or"","client":p.client or"",
    "value":p.value or 0,"year":p.year or 0,"scope":p.scope or""}
def env(n):
    v=os.environ.get(n,"")
    if v:return v
    ep=os.path.join(os.path.dirname(__file__),"..",".env")
    if os.path.exists(ep):
        with open(ep) as f:
            for l in f:
                if l.strip().startswith(f"{n}="):return l.strip().split("=",1)[1].strip().strip('"').strip("'")
    return""
def hp(p):return bcrypt.hashpw(p.encode(),bcrypt.gensalt()).decode()
def vp(p,h):return bcrypt.checkpw(p.encode(),h.encode())
def ct(u,c):return jwt.encode({"user_id":u,"company_id":c,"exp":datetime.utcnow()+timedelta(days=30)},JWT_SECRET,algorithm="HS256")
def gu(r):
    a=r.headers.get("Authorization","")
    if not a.startswith("Bearer "):raise HTTPException(401,"Not logged in")
    try:p=jwt.decode(a[7:],JWT_SECRET,algorithms=["HS256"]);return{"user_id":p["user_id"],"company_id":p["company_id"]}
    except jwt.ExpiredSignatureError:raise HTTPException(401,"Session expired")
    except:raise HTTPException(401,"Invalid token")
def check_sub(c):
    s=c.plan_status or"trial"
    if s=="active":return True
    if s=="trial" and c.trial_ends_at:return datetime.utcnow()<c.trial_ends_at
    if s=="trial":return True
    return False
def require_sub(request):
    u=gu(request);se=SessionLocal()
    try:
        c=se.query(Company).filter(Company.id==u["company_id"]).first()
        if not c or not check_sub(c):raise HTTPException(403,"Your free trial has ended. Upgrade to Pro to continue using this feature.")
    finally:se.close()
def plan_info(c):
    if not c:return{"plan_status":"trial"}
    s=c.plan_status or"trial";i={"plan_status":s}
    if s=="trial" and c.trial_ends_at:
        dl=(c.trial_ends_at-datetime.utcnow()).days;i["trial_days_left"]=max(0,dl)
        if dl<0:i["plan_status"]="expired"
    elif s=="active":i["trial_days_left"]=None
    return i

# Notifications
async def send_notif(company,opps):
    if not company.get("notify_enabled") or not opps:return
    em=company.get("notify_email","");cn=company.get("name","");ct=len(opps)
    subj=f"🔥 {ct} New Opportunit{'ies' if ct>1 else 'y'} — ConstructBid AI"
    txt="\n".join([f"• [{o.get('score',0)}pts] {o.get('title','')}" for o in opps[:5]])
    html=''.join([f'<tr><td style="padding:8px"><strong>{o.get("title","")}</strong><br><span style="color:#888">{o.get("agency","")}</span></td></tr>' for o in opps[:5]])
    html=f'<div style="background:#111;padding:20px;font-family:Arial"><h2 style="color:#22d3ee">🔥 {ct} New Opportunities for {cn}</h2><table>{html}</table></div>'
    rk=env("RESEND_API_KEY")
    if em and rk:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                await c.post("https://api.resend.com/emails",headers={"Authorization":f"Bearer {rk}","Content-Type":"application/json"},json={"from":"ConstructBid AI <onboarding@resend.dev>","to":[em],"subject":subj,"html":html,"text":txt})
        except:pass

# Weekly Digest
async def send_weekly_digest(company,stats):
    em=company.get("notify_email","");rk=env("RESEND_API_KEY")
    if not em or not rk:return
    cn=company.get("name","")
    html=f'''<div style="background:#0a0f1a;padding:20px;font-family:Arial">
    <div style="max-width:600px;margin:0 auto;background:#111827;border-radius:12px;border:1px solid #1e2d3d">
    <div style="background:linear-gradient(135deg,#3b82f6,#06b6d4);padding:24px;text-align:center">
    <h1 style="color:white;margin:0;font-size:22px">📊 Weekly Report — {cn}</h1>
    <p style="color:rgba(255,255,255,.7);margin:8px 0 0">ConstructBid AI</p></div>
    <div style="padding:24px">
    <table style="width:100%;border-collapse:collapse">
    <tr><td style="padding:12px;border-bottom:1px solid #1e2d3d;color:#64748b">New Opportunities</td><td style="padding:12px;border-bottom:1px solid #1e2d3d;text-align:right;font-weight:700;color:#e2e8f0;font-size:20px">{stats.get("new_this_week",0)}</td></tr>
    <tr><td style="padding:12px;border-bottom:1px solid #1e2d3d;color:#64748b">Worth Pursuing</td><td style="padding:12px;border-bottom:1px solid #1e2d3d;text-align:right;font-weight:700;color:#22c55e;font-size:20px">{stats.get("pursue",0)}</td></tr>
    <tr><td style="padding:12px;border-bottom:1px solid #1e2d3d;color:#64748b">Pipeline Value</td><td style="padding:12px;border-bottom:1px solid #1e2d3d;text-align:right;font-weight:700;color:#06b6d4;font-size:20px">${stats.get("pipeline",0)/1e6:.1f}M</td></tr>
    <tr><td style="padding:12px;border-bottom:1px solid #1e2d3d;color:#64748b">Bids Submitted</td><td style="padding:12px;border-bottom:1px solid #1e2d3d;text-align:right;font-weight:700;color:#e2e8f0;font-size:20px">{stats.get("submitted",0)}</td></tr>
    <tr><td style="padding:12px;color:#64748b">Win Rate</td><td style="padding:12px;text-align:right;font-weight:700;color:#22c55e;font-size:20px">{stats.get("win_rate",0)}%</td></tr>
    </table></div></div></div>'''
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            await c.post("https://api.resend.com/emails",headers={"Authorization":f"Bearer {rk}","Content-Type":"application/json"},json={"from":"ConstructBid AI <onboarding@resend.dev>","to":[em],"subject":f"📊 Weekly Report — {cn}","html":html})
        print(f"[DIGEST] Sent to {em}")
    except Exception as e:print(f"[DIGEST] Error: {e}")

# Scoring
SK=["cemetery","columbarium","gravesite","burial","headstone","memorial","interment","construction","renovation","remodel","expansion","demolition","design-build","design build","facilities","maintenance","grounds","landscaping","mowing","irrigation","hvac","plumbing","mechanical","electrical","roofing","painting","concrete","masonry","site prep","excavation","grading","paving","drainage","fencing","restoration","historic","rehabilitation","lease","leasing","repair","replace","install","upgrade","improve"]
def score(o,c):
    s=0;re=[];fl=[]
    cn=c.get("naics",[]);cc=[x.upper() for x in c.get("certifications",[])];cr=c.get("regions",[])
    cs=[x.lower() for x in c.get("services",[])];cb=c.get("bonding_capacity",0)
    on=o.get("naics","")or"";osa=o.get("set_aside","")or"";ol=o.get("location","")or""
    ov=o.get("value",0)or 0;od=o.get("due_date","")or""
    ot=((o.get("title","")or"")+" "+(o.get("scope","")or"")).lower()
    if on in cn:s+=35;re.append(f"✓ NAICS {on} — exact match")
    elif on[:4] and any(n[:4]==on[:4] for n in cn):s+=25;re.append(f"◐ NAICS {on} — related")
    elif on[:3] and any(n[:3]==on[:3] for n in cn):s+=15;re.append(f"○ NAICS {on} — same group")
    else:re.append(f"✗ NAICS {on} — outside capabilities")
    sa=osa.upper().strip();hsd=any("SDVOSB" in x for x in cc)
    if not sa or sa in("FULL & OPEN","FULL AND OPEN","NONE","N/A"):s+=15;re.append("○ Full & open")
    elif "SDVOSB" in sa:
        if hsd:s+=25;re.append("✓ SDVOSB matches")
        else:re.append("✗ SDVOSB required");fl.append("disq")
    elif "SMALL" in sa or "SBA" in sa:s+=20;re.append("✓ Small business")
    elif "8(A)" in sa or "8A" in sa:
        if any("8(A)" in x or "8A" in x for x in cc):s+=25
        else:fl.append("disq")
    elif "HUBZONE" in sa:
        if any("HUBZONE" in x for x in cc):s+=25
        else:fl.append("disq")
    elif "VOSB" in sa:
        if hsd:s+=25
        else:fl.append("disq")
    else:s+=10
    dk={w.lower() for sv in cs for w in sv.split() if len(w)>3}
    ak=set(SK)|dk;mk=[k for k in ak if k in ot]
    hs=[k for k in["cemetery","columbarium","gravesite","burial","memorial"] if k in ot]
    if hs:kw=min(20,12+len(mk)*2);re.append(f"✓ Strong: {', '.join(hs[:3])}")
    elif len(mk)>=4:kw=min(20,len(mk)*3);re.append(f"✓ Good: {', '.join(list(mk)[:5])}")
    elif len(mk)>=2:kw=min(14,len(mk)*4);re.append(f"◐ Partial: {', '.join(list(mk)[:4])}")
    elif len(mk)==1:kw=5;re.append(f"○ Weak: {mk[0]}")
    elif on in cn:kw=8;re.append("○ NAICS relevant")
    else:kw=0;re.append("✗ No keywords match")
    s+=kw
    adj={"VA":["MD","DC","WV","NC","TN"],"MD":["VA","DC","WV","PA"],"DC":["VA","MD"],"OH":["MI","IN","KY","WV","PA"],"MI":["OH","IN","WI"],"IN":["MI","OH","IL","KY"],"OK":["TX","KS","AR"],"CA":["OR","NV","AZ"],"TN":["VA","NC","GA","AL","KY"]}
    if not ol:s+=6
    elif ol in cr:s+=10;re.append(f"✓ In {ol}")
    elif any(ol in adj.get(r,[]) for r in cr):s+=5;re.append(f"◐ {ol} adjacent")
    else:s+=1;re.append(f"△ {ol} outside")
    if ov<=0:s+=4
    elif ov<=cb*.5:s+=5
    elif ov<=cb:s+=4
    elif ov<=cb*1.5:s+=2
    else:re.append(f"✗ ${ov/1e6:.1f}M exceeds bonding")
    try:
        if od:
            dl=(datetime.strptime(od,"%Y-%m-%d").date()-date.today()).days
            if dl<0:fl.append("expired")
            elif dl>30:s+=5
            elif dl>14:s+=3
            elif dl>3:s+=1
        else:s+=3
    except:s+=3
    s=min(100,s)
    if "disq" in fl:s=min(s,35);re.insert(0,"⚠ DISQUALIFIED")
    if "expired" in fl:s=min(s,25);re.insert(0,"⚠ EXPIRED")
    rc="PASS" if("disq" in fl or "expired" in fl) else "PURSUE" if s>=75 else "REVIEW" if s>=55 else "PASS"
    return{"score":s,"recommendation":rc,"reasons":re}

# SAM
SAM_URL="https://api.sam.gov/prod/opportunities/v2/search"
SA_MAP={"SBA":"Small Business","SBP":"Small Business","8A":"8(a)","8AN":"8(a)","HZC":"HUBZone","HZS":"HUBZone","SDVOSBC":"SDVOSB","SDVOSBS":"SDVOSB","VOSBC":"VOSB","VOSBS":"VOSB","":"Full & Open",None:"Full & Open"}
async def fetch_sam(company,days=30):
    ak=company.get("sam_api_key","")
    if not ak:return[]
    pf=(date.today()-timedelta(days=days)).strftime("%m/%d/%Y");pt=date.today().strftime("%m/%d/%Y")
    nc=company.get("naics",[]);pp=["8122","2362","5617","2382","5612","2379","2389"]
    pri=[n for n in nc if any(n.startswith(p) for p in pp)];oth=[n for n in nc if n not in pri]
    codes=(pri+oth)[:8];opps=[]
    se=SessionLocal()
    try:eids={o.sam_notice_id for o in se.query(Opportunity.sam_notice_id).filter(Opportunity.sam_notice_id.isnot(None),Opportunity.company_id==company["id"]).all()}
    finally:se.close()
    calls=0
    async with httpx.AsyncClient(timeout=30) as cl:
        for naics in codes:
            try:
                if calls>0:await asyncio.sleep(1.5)
                r=await cl.get(SAM_URL,params={"api_key":ak,"limit":100,"offset":0,"postedFrom":pf,"postedTo":pt,"ncode":naics,"ptype":"p,o,k"})
                calls+=1
                if r.status_code==429:break
                if r.status_code!=200:continue
                for s in r.json().get("opportunitiesData",[]):
                    nid=s.get("noticeId","")
                    if nid in eids:continue
                    eids.add(nid)
                    sa=SA_MAP.get(s.get("typeOfSetAside")or"",s.get("typeOfSetAside")or"Full & Open")
                    pop=s.get("placeOfPerformance",{})or{};ps=""
                    if pop:so=pop.get("state",{})or{};ps=so.get("code","") if isinstance(so,dict) else ""
                    dl=s.get("responseDeadLine")or"";dd=""
                    if dl:
                        for f in["%Y-%m-%d","%m/%d/%Y"]:
                            try:dd=datetime.strptime(dl[:10],f).strftime("%Y-%m-%d");break
                            except:pass
                    if dd:
                        try:
                            if datetime.strptime(dd,"%Y-%m-%d").date()<date.today():continue
                        except:pass
                    aw=s.get("award",{})or{}
                    try:v=float(aw.get("amount",0)or 0)
                    except:v=0
                    opps.append({"id":f"sam-{uuid.uuid4().hex[:8]}","company_id":company["id"],"title":(s.get("title")or"Untitled").strip(),"agency":s.get("fullParentPathName","")or"","naics":s.get("naicsCode")or naics,"location":ps or"","due_date":dd,"value":v,"set_aside":sa,"scope":(s.get("description","")or s.get("title","")or"")[:2000],"status":"new","source":"sam.gov","sam_notice_id":nid,"sam_sol_number":s.get("solicitationNumber",""),"sam_posted_date":s.get("postedDate",""),"sam_type":s.get("type",""),"sam_link":f"https://sam.gov/opp/{nid}/view" if nid else ""})
            except Exception as e:print(f"SAM error: {e}")
    return opps

# Auto-refresh + weekly digest
ARH=6;ars={"last_run":None,"next_run":None,"last_result":None,"running":False}
digest_tracker={}

async def ar_loop():
    while True:
        await asyncio.sleep(10)
        se=SessionLocal()
        try:
            for comp in se.query(Company).all():
                cd=c2d(comp)
                if not cd.get("sam_api_key")or not check_sub(comp):continue
                ars["running"]=True;ars["last_run"]=datetime.now().isoformat()
                try:
                    new=await fetch_sam(cd,30);added=0;nfy=[];nm=cd.get("notify_min_score",75)
                    for o in new:
                        sc=score(o,cd)
                        if sc["score"]>=40:se.add(Opportunity(**o));added+=1
                        if sc["score"]>=nm:nfy.append({**o,**sc})
                    se.commit()
                    if nfy:await send_notif(cd,nfy)
                    ars["last_result"]=f"Added {added} for {cd['name']}"
                except Exception as e:se.rollback();ars["last_result"]=str(e)[:100]
                ars["running"]=False

                # Weekly digest check (send on Mondays)
                cid=comp.id;today=date.today()
                if today.weekday()==0:  # Monday
                    last_sent=digest_tracker.get(cid)
                    if last_sent!=str(today) and cd.get("notify_email"):
                        opps=se.query(Opportunity).filter(Opportunity.company_id==cid).all()
                        week_ago=(datetime.utcnow()-timedelta(days=7)).isoformat()
                        new_week=sum(1 for o in opps if o.created_at and o.created_at.isoformat()>week_ago)
                        scored=[score(o2d(o),cd) for o in opps]
                        pursue=sum(1 for s in scored if s["recommendation"]=="PURSUE")
                        pipeline=sum(o.value or 0 for o in opps)
                        submitted=sum(1 for o in opps if o.status=="submitted")
                        won=sum(1 for o in opps if o.outcome=="won")
                        lost=sum(1 for o in opps if o.outcome=="lost")
                        wr=round(won/(won+lost)*100) if(won+lost)>0 else 0
                        await send_weekly_digest(cd,{"new_this_week":new_week,"pursue":pursue,"pipeline":pipeline,"submitted":submitted,"win_rate":wr})
                        digest_tracker[cid]=str(today)
        finally:se.close()
        ars["next_run"]=(datetime.now()+timedelta(hours=ARH)).isoformat()
        await asyncio.sleep(ARH*3600)

@asynccontextmanager
async def lifespan(app):
    init_db();task=asyncio.create_task(ar_loop());yield;task.cancel()

app=FastAPI(title="ConstructBid AI",version="8.0.0",lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_methods=["*"],allow_headers=["*"])

# Models
class SignupReq(BaseModel):
    email:str;password:str;company_name:str;name:Optional[str]=""
class LoginReq(BaseModel):
    email:str;password:str
class CompanyUpd(BaseModel):
    name:str;services:list[str];certifications:list[str];naics:list[str];bonding_capacity:float;regions:list[str]
    sam_api_key:Optional[str]="";notify_email:Optional[str]="";notify_phone:Optional[str]=""
    notify_enabled:Optional[bool]=False;notify_min_score:Optional[int]=75;theme:Optional[str]="dark-blue"
class OppCreate(BaseModel):
    title:str;agency:str;naics:str;location:str;due_date:str;value:float;set_aside:str;scope:str
class OppUpdate(BaseModel):
    status:Optional[str]=None;notes:Optional[str]=None;outcome:Optional[str]=None;outcome_value:Optional[float]=None
class FRReq(BaseModel):
    project_name:str;notes:str
class PropReq(BaseModel):
    section:str;opportunity_id:str
class SAMReq(BaseModel):
    days_back:Optional[int]=30;min_score:Optional[int]=40
class VoiceIn(BaseModel):
    transcript:str
class AIChatReq(BaseModel):
    opportunity_id:str;question:str

# Proposals
def gen_prop(sec,o,c,past):
    t={"executive":f"EXECUTIVE SUMMARY — DRAFT\n\n{c['name']} is pleased to submit this proposal for {o['title']} in response to {o.get('agency','')}.\n\nAs a certified {', '.join(c.get('certifications',[]))} firm, {c['name']} brings proven experience in {', '.join(c.get('services','')[:3])}.\n\nWith bonding of ${c.get('bonding_capacity',0)/1e6:.1f}M across {', '.join(c.get('regions',[]))}, we are well-positioned.\n\n[ADD: Approach]\n[ADD: Differentiators]\n[ADD: Timeline]",
    "technical":f"TECHNICAL APPROACH — DRAFT\n\nProject Understanding:\n{o.get('scope','[Not provided]')}\n\nPhase 1 — Mobilization\n- Site assessment\n- Work plan\n- Safety/QC plans\n\nPhase 2 — Execution\n- [ADD: Tasks]\n- QC inspections\n- Daily reporting\n\nPhase 3 — Closeout\n- Final inspections\n- As-built docs\n- Site restoration",
    "pastPerformance":"PAST PERFORMANCE — DRAFT\n\n"+("\n\n".join([f"{i+1}. {p['name']}\n   Client: {p['client']}\n   Value: ${p['value']:,.0f}\n   Year: {p['year']}\n   Scope: {p['scope']}" for i,p in enumerate(past)]) if past else "[No projects yet]"),
    "staffing":"STAFFING PLAN — DRAFT\n\n1. Project Manager — [Name]\n2. Superintendent — [Name]\n3. Safety Officer — [Certs]\n4. QC Manager — [Certs]",
    "compliance":"COMPLIANCE CHECKLIST\n\n"+"\n".join([f"[ ] {x} current" for x in c.get("certifications",[])])+f"\n[ ] NAICS {o.get('naics','')} confirmed\n[ ] Bonding sufficient\n[ ] Insurance current\n[ ] Licenses obtained"}
    return t.get(sec,"Not found.")

# ═══ ROUTES ═══
@app.get("/",response_class=HTMLResponse)
def landing():
    for p in[os.path.join(os.path.dirname(__file__),"..","..","landing.html"),os.path.join(os.path.dirname(__file__),"..","landing.html")]:
        if os.path.exists(p):
            with open(p) as f:return HTMLResponse(f.read())
    return HTMLResponse('<meta http-equiv="refresh" content="0;url=/dashboard">')

@app.get("/api/status")
def status():return{"app":"ConstructBid AI","version":"8.0.0","status":"running"}

@app.get("/dashboard",response_class=HTMLResponse)
def dashboard():
    for p in[os.path.join(os.path.dirname(__file__),"..","..","constructbid-ai-dashboard.html"),os.path.join(os.path.dirname(__file__),"..","constructbid-ai-dashboard.html")]:
        if os.path.exists(p):
            with open(p) as f:return HTMLResponse(f.read())
    return HTMLResponse("<h1>Dashboard not found</h1>",status_code=404)

# Auth
@app.post("/api/signup")
def signup(d:SignupReq):
    if len(d.password)<6:raise HTTPException(400,"Password 6+ chars")
    se=SessionLocal()
    try:
        if se.query(User).filter(User.email==d.email.lower().strip()).first():raise HTTPException(400,"Email taken")
        cid=f"co-{uuid.uuid4().hex[:8]}";uid=f"usr-{uuid.uuid4().hex[:8]}"
        te=datetime.utcnow()+timedelta(days=TRIAL_DAYS)
        co=Company(id=cid,name=d.company_name or"My Company",plan_status="trial",trial_ends_at=te)
        us=User(id=uid,email=d.email.lower().strip(),password_hash=hp(d.password),name=d.name or"",company_id=cid)
        se.add(co);se.add(us);se.commit()
        return{"token":ct(uid,cid),"user":{"id":uid,"email":us.email,"name":us.name,"company_id":cid},"company":{"id":cid,"name":co.name,"plan_status":"trial","trial_days_left":TRIAL_DAYS},"is_new":True}
    finally:se.close()

@app.post("/api/login")
def login(d:LoginReq):
    se=SessionLocal()
    try:
        u=se.query(User).filter(User.email==d.email.lower().strip()).first()
        if not u or not vp(d.password,u.password_hash):raise HTTPException(401,"Invalid credentials")
        co=se.query(Company).filter(Company.id==u.company_id).first()
        pi=plan_info(co)
        return{"token":ct(u.id,u.company_id),"user":{"id":u.id,"email":u.email,"name":u.name,"company_id":u.company_id},"company":{"id":co.id,"name":co.name,"theme":co.theme or"dark-blue",**pi} if co else None}
    finally:se.close()

@app.get("/api/me")
def me(request:Request):
    u=gu(request);se=SessionLocal()
    try:
        us=se.query(User).filter(User.id==u["user_id"]).first()
        co=se.query(Company).filter(Company.id==u["company_id"]).first()
        if not us:raise HTTPException(404)
        pi=plan_info(co) if co else{}
        return{"user":{"id":us.id,"email":us.email,"name":us.name,"company_id":us.company_id},"company":{"id":co.id,"name":co.name,"theme":co.theme or"dark-blue",**pi} if co else None}
    finally:se.close()

# Onboarding status
@app.get("/api/onboarding")
def onboarding(request:Request):
    u=gu(request);se=SessionLocal()
    try:
        co=se.query(Company).filter(Company.id==u["company_id"]).first()
        if not co:return{"steps":[],"complete":0,"total":5}
        cd=c2d(co)
        steps=[
            {"key":"name","label":"Add your company name","done":bool(cd["name"] and cd["name"]!="New Company" and cd["name"]!="My Company"),"tip":"Go to Company tab and enter your real company name"},
            {"key":"naics","label":"Add NAICS codes","done":len(cd["naics"])>0,"tip":"Your NAICS codes tell us what contracts to find. Look them up at naics.com"},
            {"key":"certs","label":"Add certifications","done":len(cd["certifications"])>0,"tip":"SDVOSB, 8(a), HUBZone, etc. These determine which set-asides you qualify for"},
            {"key":"regions","label":"Set operating regions","done":len(cd["regions"])>0,"tip":"Which states do you work in? We'll prioritize opportunities there"},
            {"key":"sam","label":"Connect SAM.gov API key","done":bool(cd["sam_api_key"]),"tip":"Free from sam.gov → Profile → Public API Key. This lets us auto-find opportunities"},
        ]
        complete=sum(1 for s in steps if s["done"])
        return{"steps":steps,"complete":complete,"total":len(steps)}
    finally:se.close()

# AI Chat
@app.post("/api/ask-ai")
async def ask_ai(req:AIChatReq,request:Request):
    u=gu(request);se=SessionLocal()
    try:
        o=se.query(Opportunity).filter(Opportunity.id==req.opportunity_id,Opportunity.company_id==u["company_id"]).first()
        if not o:raise HTTPException(404)
        co=se.query(Company).filter(Company.id==u["company_id"]).first()
        cd=c2d(co) if co else{};od=o2d(o);sc=score(od,cd)
    finally:se.close()

    ak=env("ANTHROPIC_API_KEY")
    if not ak:raise HTTPException(400,"AI not configured. Add ANTHROPIC_API_KEY to environment.")

    prompt=f"""You are an expert government contracting advisor helping a contractor evaluate a bid opportunity.

Company Profile:
- Name: {cd.get('name','')}
- Services: {', '.join(cd.get('services',[]))}
- Certifications: {', '.join(cd.get('certifications',[]))}
- NAICS Codes: {', '.join(cd.get('naics',[]))}
- Bonding: ${cd.get('bonding_capacity',0)/1e6:.1f}M
- Regions: {', '.join(cd.get('regions',[]))}

Opportunity:
- Title: {od.get('title','')}
- Agency: {od.get('agency','')}
- NAICS: {od.get('naics','')}
- Set-Aside: {od.get('set_aside','')}
- Location: {od.get('location','')}
- Value: ${od.get('value',0)/1e6:.1f}M
- Due Date: {od.get('due_date','')}
- Scope: {od.get('scope','')}

AI Score: {sc['score']}/100 ({sc['recommendation']})
Reasons: {'; '.join(sc['reasons'])}

Answer the user's question concisely and specifically about this opportunity. Be direct and actionable. If they ask "can we win this?" give honest odds and explain why.

User question: {req.question}"""

    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r=await c.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key":ak,"anthropic-version":"2023-06-01","content-type":"application/json"},
                json={"model":"claude-sonnet-4-20250514","max_tokens":800,"messages":[{"role":"user","content":prompt}]})
            if r.status_code==200:
                txt="".join(b["text"] for b in r.json().get("content",[]) if b.get("type")=="text")
                return{"answer":txt,"score":sc["score"],"recommendation":sc["recommendation"]}
            else:
                raise HTTPException(500,f"AI error: {r.status_code}")
    except HTTPException:raise
    except Exception as e:raise HTTPException(500,f"AI error: {str(e)[:100]}")

# Company
@app.get("/api/company")
def get_co(request:Request):
    u=gu(request);se=SessionLocal()
    try:
        c=se.query(Company).filter(Company.id==u["company_id"]).first()
        if not c:raise HTTPException(404)
        d=c2d(c);k=d.get("sam_api_key","");d["sam_api_key_set"]=bool(k);d["sam_api_key_preview"]=k[:6]+"..." if len(k)>6 else k
        d.update(plan_info(c));return d
    finally:se.close()

@app.put("/api/company")
def upd_co(data:CompanyUpd,request:Request):
    u=gu(request);se=SessionLocal()
    try:
        c=se.query(Company).filter(Company.id==u["company_id"]).first()
        if not c:raise HTTPException(404)
        for k,v in data.dict().items():setattr(c,k,v)
        c.updated_at=datetime.utcnow();se.commit();return c2d(c)
    finally:se.close()

# Opportunities
@app.get("/api/opportunities")
def list_opps(request:Request):
    u=gu(request);se=SessionLocal()
    try:
        c=se.query(Company).filter(Company.id==u["company_id"]).first();cd=c2d(c) if c else{}
        opps=se.query(Opportunity).filter(Opportunity.company_id==u["company_id"]).all()
        return sorted([{**o2d(o),**score(o2d(o),cd)} for o in opps],key=lambda x:x["score"],reverse=True)
    finally:se.close()

@app.post("/api/opportunities")
def create_opp(data:OppCreate,request:Request):
    u=gu(request);se=SessionLocal()
    try:
        o=Opportunity(id=f"opp-{uuid.uuid4().hex[:8]}",company_id=u["company_id"],**data.dict(),source="manual")
        se.add(o);se.commit()
        c=se.query(Company).filter(Company.id==u["company_id"]).first()
        return{**o2d(o),**score(o2d(o),c2d(c) if c else{})}
    finally:se.close()

@app.put("/api/opportunities/{oid}")
def update_opp(oid:str,data:OppUpdate,request:Request):
    u=gu(request);se=SessionLocal()
    try:
        o=se.query(Opportunity).filter(Opportunity.id==oid,Opportunity.company_id==u["company_id"]).first()
        if not o:raise HTTPException(404)
        if data.status is not None:o.status=data.status
        if data.notes is not None:o.notes=data.notes
        if data.outcome is not None:o.outcome=data.outcome
        if data.outcome_value is not None:o.outcome_value=data.outcome_value
        se.commit();return o2d(o)
    finally:se.close()

@app.delete("/api/opportunities/{oid}")
def del_opp(oid:str,request:Request):
    u=gu(request);se=SessionLocal()
    try:
        o=se.query(Opportunity).filter(Opportunity.id==oid,Opportunity.company_id==u["company_id"]).first()
        if not o:raise HTTPException(404)
        se.delete(o);se.commit();return{"deleted":True}
    finally:se.close()

# Analytics
@app.get("/api/analytics")
def analytics(request:Request):
    u=gu(request);se=SessionLocal()
    try:
        c=se.query(Company).filter(Company.id==u["company_id"]).first();cd=c2d(c) if c else{}
        opps=se.query(Opportunity).filter(Opportunity.company_id==u["company_id"]).all()
        total=len(opps);by_status={};by_outcome={};tv=0;wv=0
        for o in opps:
            st=o.status or"new";oc=o.outcome or""
            by_status[st]=by_status.get(st,0)+1
            if oc:by_outcome[oc]=by_outcome.get(oc,0)+1
            tv+=o.value or 0
            if oc=="won":wv+=o.outcome_value or o.value or 0
        scored=[score(o2d(o),cd) for o in opps]
        pursue=sum(1 for s in scored if s["recommendation"]=="PURSUE")
        review=sum(1 for s in scored if s["recommendation"]=="REVIEW")
        won=by_outcome.get("won",0);lost=by_outcome.get("lost",0)
        wr=round(won/(won+lost)*100) if(won+lost)>0 else 0
        return{"total":total,"by_status":by_status,"by_outcome":by_outcome,"pursue":pursue,"review":review,"total_pipeline_value":tv,"won_value":wv,"win_rate":wr,"won":won,"lost":lost,"submitted":by_status.get("submitted",0)}
    finally:se.close()

# SAM
@app.post("/api/sam-refresh")
async def sam_refresh(req:SAMReq,request:Request):
    u=gu(request);se=SessionLocal()
    try:
        c=se.query(Company).filter(Company.id==u["company_id"]).first()
        if not c:raise HTTPException(404)
        cd=c2d(c)
        if not cd.get("sam_api_key"):raise HTTPException(400,"SAM.gov API key not set.")
        old=se.query(Opportunity).filter(Opportunity.source=="sam.gov",Opportunity.company_id==u["company_id"]).count()
        se.query(Opportunity).filter(Opportunity.source=="sam.gov",Opportunity.company_id==u["company_id"]).delete();se.commit()
    finally:se.close()
    new=await fetch_sam(cd,req.days_back);scored=[];added=0
    se=SessionLocal()
    try:
        for o in new:
            s=score(o,cd)
            if s["score"]>=req.min_score:scored.append({**o,**s});se.add(Opportunity(**o));added+=1
        se.commit()
    finally:se.close()
    return{"cleared":old,"fetched":len(new),"added":added}

@app.post("/api/clear-expired")
def clear_exp(request:Request):
    u=gu(request);se=SessionLocal()
    try:
        ts=date.today().strftime("%Y-%m-%d");opps=se.query(Opportunity).filter(Opportunity.company_id==u["company_id"],Opportunity.due_date<ts,Opportunity.due_date!="").all()
        rm=len(opps)
        for o in opps:se.delete(o)
        se.commit();return{"removed":rm}
    finally:se.close()

@app.post("/api/clear-passes")
def clear_pass(request:Request):
    u=gu(request);se=SessionLocal()
    try:
        c=se.query(Company).filter(Company.id==u["company_id"]).first();cd=c2d(c) if c else{}
        opps=se.query(Opportunity).filter(Opportunity.company_id==u["company_id"],Opportunity.source=="sam.gov").all()
        rm=0
        for o in opps:
            if score(o2d(o),cd)["recommendation"]=="PASS":se.delete(o);rm+=1
        se.commit();return{"removed":rm}
    finally:se.close()

@app.post("/api/proposal")
def proposal(req:PropReq,request:Request):
    u=gu(request);se=SessionLocal()
    try:
        o=se.query(Opportunity).filter(Opportunity.id==req.opportunity_id,Opportunity.company_id==u["company_id"]).first()
        if not o:raise HTTPException(404)
        c=se.query(Company).filter(Company.id==u["company_id"]).first()
        ps=[p2d(p) for p in se.query(Project).filter(Project.company_id==u["company_id"]).all()]
        return{"section":req.section,"content":gen_prop(req.section,o2d(o),c2d(c) if c else{},ps)}
    finally:se.close()

@app.post("/api/field-report")
def field_report(req:FRReq,request:Request):
    gu(request);today=datetime.now().strftime("%A, %B %d, %Y")
    return{"report":f"DAILY FIELD REPORT\n{'━'*40}\nDate: {today}\nProject: {req.project_name}\nPrepared by: [Name]\n\n─── WORK PERFORMED ───\n{req.notes}\n\n─── LABOR ───\n• Crew: [#]\n• Subs: [List]\n\n─── ISSUES ───\n• [Describe]\n\n─── SAFETY ───\n• Incidents: None\n\n─── TOMORROW ───\n• [Plan]\n{'━'*40}"}

@app.get("/api/projects")
def projects(request:Request):
    u=gu(request);se=SessionLocal()
    try:return[p2d(p) for p in se.query(Project).filter(Project.company_id==u["company_id"]).all()]
    finally:se.close()

@app.get("/api/auto-refresh-status")
def ar_status():return ars

@app.post("/api/test-notification")
async def test_notif(request:Request):
    u=gu(request);se=SessionLocal()
    try:
        c=se.query(Company).filter(Company.id==u["company_id"]).first()
        if not c:raise HTTPException(404)
        cd=c2d(c)
        if not cd.get("notify_enabled"):raise HTTPException(400,"Notifications not enabled")
    finally:se.close()
    await send_notif(cd,[{"title":"TEST Notification","agency":"ConstructBid AI","score":99,"recommendation":"PURSUE","value":2500000,"due_date":"2026-05-01"}])
    return{"sent":True}

# Stripe
@app.post("/api/create-checkout")
def checkout(request:Request):
    u=gu(request)
    if not STRIPE_SECRET or not STRIPE_PRICE_ID:raise HTTPException(500,"Stripe not configured")
    se=SessionLocal()
    try:
        co=se.query(Company).filter(Company.id==u["company_id"]).first()
        us=se.query(User).filter(User.id==u["user_id"]).first()
        if not co:raise HTTPException(404)
        if co.stripe_customer_id:cust_id=co.stripe_customer_id
        else:
            cust=stripe.Customer.create(email=us.email,name=co.name,metadata={"company_id":co.id})
            co.stripe_customer_id=cust.id;se.commit();cust_id=cust.id
        base=str(request.base_url).rstrip("/")
        sess=stripe.checkout.Session.create(customer=cust_id,payment_method_types=["card"],line_items=[{"price":STRIPE_PRICE_ID,"quantity":1}],mode="subscription",success_url=f"{base}/dashboard?billing=success",cancel_url=f"{base}/dashboard",metadata={"company_id":co.id})
        return{"checkout_url":sess.url}
    finally:se.close()

@app.post("/api/billing-portal")
def portal(request:Request):
    u=gu(request);se=SessionLocal()
    try:
        co=se.query(Company).filter(Company.id==u["company_id"]).first()
        if not co or not co.stripe_customer_id:raise HTTPException(400,"No billing account")
        base=str(request.base_url).rstrip("/")
        p=stripe.billing_portal.Session.create(customer=co.stripe_customer_id,return_url=f"{base}/dashboard")
        return{"portal_url":p.url}
    finally:se.close()

@app.post("/api/stripe-webhook")
async def webhook(request:Request):
    body=await request.body();sig=request.headers.get("stripe-signature","")
    try:
        if STRIPE_WEBHOOK_SECRET:event=stripe.Webhook.construct_event(body,sig,STRIPE_WEBHOOK_SECRET)
        else:event=json.loads(body)
    except:raise HTTPException(400,"Bad webhook")
    et=event.get("type","") if isinstance(event,dict) else event.type
    d=event.get("data",{}).get("object",{}) if isinstance(event,dict) else event.data.object
    se=SessionLocal()
    try:
        if et=="checkout.session.completed":
            cid=d.get("metadata",{}).get("company_id","")
            if cid:
                co=se.query(Company).filter(Company.id==cid).first()
                if co:co.plan_status="active";co.stripe_subscription_id=d.get("subscription","");se.commit()
        elif et in("customer.subscription.deleted","customer.subscription.paused"):
            sid=d.get("id","")
            if sid:
                co=se.query(Company).filter(Company.stripe_subscription_id==sid).first()
                if co:co.plan_status="cancelled";se.commit()
        elif et=="invoice.payment_failed":
            cust=d.get("customer","")
            if cust:
                co=se.query(Company).filter(Company.stripe_customer_id==cust).first()
                if co:co.plan_status="expired";se.commit()
    finally:se.close()
    return{"received":True}

# Voice
VP="""Parse spoken company description into JSON. Fix speech errors. Return ONLY JSON:
{"name":"","services":[],"certifications":[],"regions":[],"bonding_capacity":0,"naics":[]}
SDVOSB often heard as "STV OSB". States→2-letter codes. Bonding in dollars. Only fields with data."""

@app.post("/api/parse-voice-profile")
async def voice(data:VoiceIn,request:Request):
    gu(request);ak=env("ANTHROPIC_API_KEY")
    if ak:
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r=await c.post("https://api.anthropic.com/v1/messages",headers={"x-api-key":ak,"anthropic-version":"2023-06-01","content-type":"application/json"},json={"model":"claude-sonnet-4-20250514","max_tokens":1000,"messages":[{"role":"user","content":VP+"\n\nTranscript:\n"+data.transcript}]})
                if r.status_code==200:
                    txt="".join(b["text"] for b in r.json().get("content",[]) if b.get("type")=="text")
                    txt=re.sub(r'^```(?:json)?\s*','',txt.strip());txt=re.sub(r'\s*```$','',txt)
                    p=json.loads(txt);result={k:v for k,v in p.items() if v}
                    if "bonding_capacity" in result:result["bonding_capacity"]=int(result["bonding_capacity"])
                    return{"parsed":result,"transcript":data.transcript,"fields_found":len(result),"method":"ai"}
        except Exception as e:print(f"Voice error: {e}")
    return{"parsed":{},"transcript":data.transcript,"fields_found":0,"method":"fallback"}

if __name__=="__main__":
    import uvicorn;uvicorn.run(app,host="0.0.0.0",port=8000)

# ── UEI Auto-Fill (SAM.gov Entity API) ──
class UEILookup(BaseModel):
    uei: str

@app.get("/api/team")
async def get_team(request: Request):
    u=gu(request);se=SessionLocal()
    try:
        members=se.query(User).filter(User.company_id==u["company_id"]).all()
        return [{"id":m.id,"email":m.email,"name":m.name,"role":getattr(m,"role","admin"),"created_at":m.created_at.isoformat() if m.created_at else ""} for m in members]
    finally:se.close()

@app.post("/api/invite-member")
async def invite_member(request: Request):
    u=gu(request);se=SessionLocal()
    try:
        admin=se.query(User).filter(User.id==u["user_id"]).first()
        if not admin or getattr(admin,"role","admin")!="admin":raise HTTPException(403,"Only admins can invite members")
        d=await request.json()
        email=d.get("email","").lower().strip()
        name=d.get("name","")
        password=d.get("password","")
        if not email or not password:raise HTTPException(400,"Email and password required")
        if len(password)<6:raise HTTPException(400,"Password must be 6+ characters")
        existing=se.query(User).filter(User.email==email).first()
        if existing:raise HTTPException(400,"Email already in use")
        import uuid as uu
        mid=uu.uuid4().hex[:12]
        member=User(id=mid,email=email,password_hash=hp(password),name=name,company_id=u["company_id"],role="member")
        se.add(member);se.commit()
        return{"ok":True,"id":mid,"email":email}
    finally:se.close()

@app.post("/api/remove-member")
async def remove_member(request: Request):
    u=gu(request);se=SessionLocal()
    try:
        admin=se.query(User).filter(User.id==u["user_id"]).first()
        if not admin or getattr(admin,"role","admin")!="admin":raise HTTPException(403,"Only admins can remove members")
        d=await request.json()
        mid=d.get("member_id","")
        if mid==u["user_id"]:raise HTTPException(400,"Cannot remove yourself")
        member=se.query(User).filter(User.id==mid,User.company_id==u["company_id"]).first()
        if not member:raise HTTPException(404,"Member not found")
        se.delete(member);se.commit()
        return{"ok":True}
    finally:se.close()

@app.post("/api/update-account")
async def update_account(request: Request):
    u=gu(request);se=SessionLocal()
    try:
        user=se.query(User).filter(User.id==u["user_id"]).first()
        if not user:raise HTTPException(404,"User not found")
        d=await request.json()
        if d.get("email"):
            existing=se.query(User).filter(User.email==d["email"]).first()
            if existing and existing.id!=user.id:raise HTTPException(400,"Email already in use")
            user.email=d["email"]
        if d.get("password"):
            if len(d["password"])<6:raise HTTPException(400,"Password must be 6+ characters")
            user.password_hash=bcrypt.hashpw(d["password"].encode(),bcrypt.gensalt()).decode()
        se.commit()
        return{"ok":True}
    finally:se.close()

@app.post("/api/award-history")
async def award_history(request: Request):
    gu(request)
    d = await request.json()
    naics = d.get("naics", "")
    agency = d.get("agency", "")
    keyword = d.get("keyword", "")
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            filters = {"time_period": [{"start_date": "2024-01-01", "end_date": "2026-12-31"}]}
            if naics:
                filters["naics_codes"] = [naics]
            # Skip agency filter - SAM.gov names don't match USASpending names
            # Search by NAICS only for reliable results
            body = {
                "filters": {**filters, "award_type_codes": ["A", "B", "C", "D"]},
                "fields": ["Award ID", "Recipient Name", "Award Amount", "Start Date", "End Date", "Awarding Agency", "NAICS Code", "Award Type", "Description"],
                "limit": 15,
                "order": "desc",
                "sort": "Award Amount",
                "subawards": False
            }
            r = await c.post("https://api.usaspending.gov/api/v2/search/spending_by_award/", json=body)
            if r.status_code == 200:
                data = r.json()
                results = []
                for a in data.get("results", []):
                    results.append({
                        "award_id": a.get("Award ID", ""),
                        "recipient": a.get("Recipient Name", ""),
                        "amount": a.get("Award Amount", 0),
                        "start_date": a.get("Start Date", ""),
                        "end_date": a.get("End Date", ""),
                        "agency": a.get("Awarding Agency", ""),
                        "naics": a.get("NAICS Code", ""),
                        "type": a.get("Award Type", ""),
                        "description": (a.get("Description", "") or "")[:200]
                    })
                total = data.get("page_metadata", {}).get("total", 0)
                return {"results": results, "total": total}
            return {"results": [], "total": 0}
    except Exception as ex:
        return {"results": [], "total": 0, "error": str(ex)[:100]}

@app.post("/api/lookup-uei")
async def lookup_uei(data: UEILookup, request: Request):
    gu(request)
    uei = data.uei.strip().upper()
    if not uei or len(uei) < 10:
        raise HTTPException(400, "Enter a valid UEI (12 characters)")
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            se=SessionLocal()
            try:
                comp=se.query(Company).filter(Company.id==gu(request)["company_id"]).first()
                sam_key=(comp.sam_api_key if comp else "") or env("SAM_ENTITY_API_KEY") or ""
            finally:se.close()
            r = await c.get("https://api.sam.gov/entity-information/v2/entities", params={"ueiSAM": uei, "api_key": sam_key}, headers={"Accept": "application/json"})
            if r.status_code == 200:
                entities = r.json().get("entityData", [])
                if entities:
                    e = entities[0]; reg = e.get("entityRegistration", {}); core = e.get("coreData", {}); assertions = e.get("assertions", {})
                    name = reg.get("legalBusinessName", "") or reg.get("dbaName", "")
                    phys = core.get("physicalAddress", {}) or {}; state = phys.get("stateOrProvinceCode", "")
                    naics_list = [str(n.get("naicsCode", "")) for n in (assertions.get("goodsAndServices", {}).get("naicsList", []) or []) if n.get("naicsCode")]
                    certs = []
                    for b in (core.get("businessTypes", {}).get("businessTypeList", []) or []):
                        bt = b.get("businessTypeDesc", "")
                        btl = bt.lower().replace("-"," ")
                        if "service disabled veteran" in btl: certs.append("SDVOSB")
                        elif "veteran" in btl and "owned" in btl: certs.append("VOSB")
                        elif "8(a)" in bt: certs.append("8(a)")
                        elif "hubzone" in btl: certs.append("HUBZone")
                        elif "woman owned" in btl: certs.append("WOSB")
                    return {"found": True, "name": name, "uei": uei, "cage": reg.get("cageCode", ""), "state": state, "naics": naics_list, "certifications": list(set(certs)), "regions": [state] if state else []}
            return {"found": False, "message": "UEI not found. Check the number."}
    except Exception as ex:
        return {"found": False, "message": f"SAM.gov error: {str(ex)[:100]}"}
