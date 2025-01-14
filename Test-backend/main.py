from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy import create_engine, func, select, Index, and_, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, DeclarativeBase, Mapped, mapped_column, sessionmaker
from datetime import datetime, timedelta, date
from typing import List, Optional, Dict, Any
import asyncio
import random
import time
from concurrent.futures import ThreadPoolExecutor
from fastapi.middleware.cors import CORSMiddleware

# Base Models
class Base(DeclarativeBase):
    pass

class CareNote(Base):
    __tablename__ = "care_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(index=True)
    facility_id: Mapped[int] = mapped_column(index=True)
    patient_id: Mapped[str]
    category: Mapped[str]  # 'medication', 'observation', 'treatment'
    priority: Mapped[int]  # 1-5
    created_at: Mapped[datetime] = mapped_column(index=True)
    created_by: Mapped[str]
    note_content: Mapped[str]

    __table_args__ = (
        Index('idx_tenant_facility_date', 'tenant_id', 'facility_id', 'created_at'),
        Index('idx_tenant_date_category', 'tenant_id', 'created_at', 'category'),
    )

# Database setup
DATABASE_URL = "sqlite:///carenotes.db"
ASYNC_DATABASE_URL = "sqlite+aiosqlite:///carenotes.db"

# Sync engine for simpler setup
sync_engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})

# Async engine for async operations
async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False}
)
async_session_maker = async_sessionmaker(async_engine, expire_on_commit=False)

app = FastAPI()

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Frontend URL
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# Dependency to get DB session
async def get_db():
    async with async_session_maker() as session:
        yield session

# Initialize database
def init_db():
    Base.metadata.drop_all(bind=sync_engine)
    Base.metadata.create_all(bind=sync_engine)

# Async function to populate test data
async def create_test_data(num_notes: int = 100000):
    """Populate database with test care notes using efficient batch insertion."""
    categories = ['medication', 'observation', 'treatment']
    tenants = list(range(1, 11))  # 10 tenants
    facilities_per_tenant = 5
    patients_per_facility = 50

    batch_size = 1000
    notes_data = []

    # Generate timestamps for the last 30 days
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=30)

    print(f"Generating {num_notes} care notes...")

    for i in range(num_notes):
        tenant_id = random.choice(tenants)
        facility_id = tenant_id * 10 + random.randint(1, facilities_per_tenant)
        patient_id = f"P{facility_id}_{random.randint(1, patients_per_facility)}"

        # Realistic timestamp distribution (more recent notes)
        days_ago = random.triangular(0, 30, 5)
        timestamp = end_date - timedelta(days=days_ago,
                                       hours=random.randint(0, 23),
                                       minutes=random.randint(0, 59))

        note = {
            'tenant_id': tenant_id,
            'facility_id': facility_id,
            'patient_id': patient_id,
            'category': random.choice(categories),
            'priority': random.randint(1, 5),
            'created_at': timestamp,
            'created_by': f"nurse_{random.randint(1, 20)}",
            'note_content': f"Test care note content for patient {patient_id}"
        }
        notes_data.append(note)

        # Batch insert
        if len(notes_data) >= batch_size:
            async with async_session_maker() as session:
                await session.execute(
                    text("""
                    INSERT INTO care_notes (tenant_id, facility_id, patient_id, category, priority, created_at, created_by, note_content)
                    VALUES (:tenant_id, :facility_id, :patient_id, :category, :priority, :created_at, :created_by, :note_content)
                    """),
                    notes_data
                )
                await session.commit()
            notes_data = []
            if (i + 1) % 10000 == 0:
                print(f"Inserted {i + 1} notes...")

    # Insert remaining notes
    if notes_data:
        async with async_session_maker() as session:
            await session.execute(
                text("""
                INSERT INTO care_notes (tenant_id, facility_id, patient_id, category, priority, created_at, created_by, note_content)
                VALUES (:tenant_id, :facility_id, :patient_id, :category, :priority, :created_at, :created_by, :note_content)
                """),
                notes_data
            )
            await session.commit()

    print(f"Successfully inserted {num_notes} care notes!")

