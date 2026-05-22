"""Database models for ConstructBid AI v7."""
import os
from sqlalchemy import create_engine, Column, String, Float, Integer, Boolean, Text, DateTime, JSON
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+psycopg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None
SessionLocal = sessionmaker(bind=engine) if engine else None
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    name = Column(String, default="")
    company_id = Column(String, nullable=False)
    role = Column(String, default="admin")
    created_at = Column(DateTime, default=datetime.utcnow)

class Document(Base):
    __tablename__ = "documents"
    id = Column(String, primary_key=True)
    company_id = Column(String, nullable=False)
    name = Column(String, default="")
    category = Column(String, default="other")
    notes = Column(Text, default="")
    file_url = Column(String, default="")
    file_data = Column(Text, default="")
    file_type = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

class SavedSearch(Base):
    __tablename__ = "saved_searches"
    id = Column(String, primary_key=True)
    company_id = Column(String, nullable=False)
    name = Column(String, default="")
    filters = Column(JSON, default=dict)
    alert_enabled = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Company(Base):
    __tablename__ = "companies"
    id = Column(String, primary_key=True)
    name = Column(String, default="New Company")
    services = Column(JSON, default=list)
    certifications = Column(JSON, default=list)
    naics = Column(JSON, default=list)
    bonding_capacity = Column(Float, default=0)
    regions = Column(JSON, default=list)
    sam_api_key = Column(String, default="")
    notify_email = Column(String, default="")
    notify_phone = Column(String, default="")
    notify_enabled = Column(Boolean, default=False)
    notify_min_score = Column(Integer, default=75)
    stripe_customer_id = Column(String, default="")
    stripe_subscription_id = Column(String, default="")
    plan_status = Column(String, default="trial")
    trial_ends_at = Column(DateTime, nullable=True)
    theme = Column(String, default="dark-blue")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Opportunity(Base):
    __tablename__ = "opportunities"
    id = Column(String, primary_key=True)
    company_id = Column(String, default="")
    title = Column(String, default="")
    agency = Column(String, default="")
    naics = Column(String, default="")
    location = Column(String, default="")
    due_date = Column(String, default="")
    value = Column(Float, default=0)
    set_aside = Column(String, default="")
    scope = Column(Text, default="")
    status = Column(String, default="new")  # new, pursuing, submitted, won, lost, passed
    source = Column(String, default="manual")
    notes = Column(Text, default="")
    outcome = Column(String, default="")  # won, lost, no-bid, pending
    outcome_value = Column(Float, default=0)  # actual award value
    sam_notice_id = Column(String, nullable=True)
    sam_sol_number = Column(String, default="")
    sam_posted_date = Column(String, default="")
    sam_type = Column(String, default="")
    sam_link = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

class Project(Base):
    __tablename__ = "projects"
    id = Column(String, primary_key=True)
    company_id = Column(String, default="")
    name = Column(String, default="")
    client = Column(String, default="")
    value = Column(Float, default=0)
    year = Column(Integer, default=2024)
    scope = Column(Text, default="")

def init_db():
    if not engine: print("[DB] No DATABASE_URL"); return
    Base.metadata.create_all(engine)
    print("[DB] Tables created/verified")
