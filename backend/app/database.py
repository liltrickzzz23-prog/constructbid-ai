"""
Database models and setup for ConstructBid AI.
Uses SQLAlchemy with PostgreSQL (psycopg3 driver).
"""

import os
from sqlalchemy import create_engine, Column, String, Float, Integer, Boolean, Text, DateTime, JSON
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Fix Railway's postgres:// to work with psycopg3
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None
SessionLocal = sessionmaker(bind=engine) if engine else None
Base = declarative_base()


class Company(Base):
    __tablename__ = "companies"

    id = Column(String, primary_key=True, default="default")
    name = Column(String, default="Your Company Name")
    services = Column(JSON, default=list)
    certifications = Column(JSON, default=list)
    naics = Column(JSON, default=list)
    bonding_capacity = Column(Float, default=5000000)
    regions = Column(JSON, default=list)
    sam_api_key = Column(String, default="")
    notify_email = Column(String, default="")
    notify_phone = Column(String, default="")
    notify_enabled = Column(Boolean, default=False)
    notify_min_score = Column(Integer, default=75)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Opportunity(Base):
    __tablename__ = "opportunities"

    id = Column(String, primary_key=True)
    company_id = Column(String, default="default")
    title = Column(String, default="")
    agency = Column(String, default="")
    naics = Column(String, default="")
    location = Column(String, default="")
    due_date = Column(String, default="")
    value = Column(Float, default=0)
    set_aside = Column(String, default="")
    scope = Column(Text, default="")
    status = Column(String, default="new")
    source = Column(String, default="manual")
    sam_notice_id = Column(String, nullable=True)
    sam_sol_number = Column(String, default="")
    sam_posted_date = Column(String, default="")
    sam_type = Column(String, default="")
    sam_link = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class Project(Base):
    __tablename__ = "projects"

    id = Column(String, primary_key=True)
    company_id = Column(String, default="default")
    name = Column(String, default="")
    client = Column(String, default="")
    value = Column(Float, default=0)
    year = Column(Integer, default=2024)
    scope = Column(Text, default="")


def init_db():
    if not engine:
        print("[DB] No DATABASE_URL set — database not initialized")
        return

    Base.metadata.create_all(engine)
    print("[DB] Tables created/verified")

    session = SessionLocal()
    try:
        if session.query(Company).count() == 0:
            default_company = Company(
                id="default",
                name="Your Company Name",
                services=["General Construction", "Facilities Maintenance", "Cemetery Operations",
                          "Site Preparation", "HVAC/Plumbing", "Landscaping", "Design-Build"],
                certifications=["SDVOSB", "OSHA 30", "EPA Lead-Safe"],
                naics=["236220", "236210", "237110", "237310", "238220", "561730"],
                bonding_capacity=5000000,
                regions=["VA", "MD", "DC", "NC", "WV"],
            )
            session.add(default_company)
            session.commit()
            print("[DB] Default company created")

        if session.query(Project).count() == 0:
            samples = [
                Project(id="proj-1", company_id="default",
                        name="Abraham Lincoln National Cemetery – Section 40 Expansion",
                        client="NCA / VA", value=3100000, year=2024,
                        scope="Gravesite expansion with 5,000 new burial sites, roads, drainage, irrigation"),
                Project(id="proj-2", company_id="default",
                        name="Fort Belvoir – Building 1442 Renovation",
                        client="US Army", value=1800000, year=2023,
                        scope="Complete interior renovation, HVAC replacement, ADA upgrades"),
            ]
            session.add_all(samples)
            session.commit()
            print("[DB] Sample projects created")
    finally:
        session.close()


def get_db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
