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

# Original inefficient implementation
async def get_daily_care_stats(
    db: AsyncSession,
    tenant_id: int,
    facility_ids: Optional[List[int]] = None,
    date: Optional[datetime] = None
):
    if date is None:
        date = datetime.utcnow()

    # Current inefficient query
    base_query = select(CareNote).where(
        CareNote.tenant_id == tenant_id,
        func.date(CareNote.created_at) == date.date()
    )

    if facility_ids:
        base_query = base_query.where(CareNote.facility_id.in_(facility_ids))

    notes = (await db.execute(base_query)).scalars().all()

    # Inefficient in-memory processing
    stats = {
        "total_notes": len(notes),
        "by_category": {},
        "by_priority": {1: 0, 2: 0, 3: 0, 4: 0, 5: 0},
        "by_facility": {},
        "avg_notes_per_patient": 0
    }

    for note in notes:
        stats["by_category"][note.category] = stats["by_category"].get(note.category, 0) + 1
        stats["by_priority"][note.priority] += 1
        stats["by_facility"][note.facility_id] = stats["by_facility"].get(note.facility_id, 0) + 1

    return stats

# Optimized implementation using SQL aggregations
async def get_daily_care_stats_optimized(
    db: AsyncSession,
    tenant_id: int,
    facility_ids: Optional[List[int]] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> Dict[str, Any]:
    """Optimized version using SQL aggregations and single query."""
    if start_date is None:
        start_date = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    if end_date is None:
        end_date = datetime.utcnow().replace(hour=23, minute=59, second=59, microsecond=999999)

    print(f"Calculating stats for tenant {tenant_id}, facilities {facility_ids}, date range: {start_date} to {end_date}")

    # Build base filter conditions
    filters = [
        CareNote.tenant_id == tenant_id,
        CareNote.created_at >= start_date,
        CareNote.created_at <= end_date
    ]

    if facility_ids:
        filters.append(CareNote.facility_id.in_(facility_ids))

    print(f"Using filters: {filters}")

    # Multiple optimized queries (SQLite doesn't support GROUPING SETS)
    # Get total count and unique patients
    total_query = (
        select(
            func.count(CareNote.id).label('total_notes'),
            func.count(func.distinct(CareNote.patient_id)).label('unique_patients')
        )
        .where(and_(*filters))
    )
    total_result = (await db.execute(total_query)).first()
    print(f"Total query result: {total_result}")

    # Get counts by category
    category_query = (
        select(
            CareNote.category,
            func.count(CareNote.id).label('count')
        )
        .where(and_(*filters))
        .group_by(CareNote.category)
    )
    category_results = (await db.execute(category_query)).all()
    print(f"Category results: {category_results}")

    # Get counts by priority
    priority_query = (
        select(
            CareNote.priority,
            func.count(CareNote.id).label('count')
        )
        .where(and_(*filters))
        .group_by(CareNote.priority)
    )
    priority_results = (await db.execute(priority_query)).all()
    print(f"Priority results: {priority_results}")

    # Get counts by facility
    facility_query = (
        select(
            CareNote.facility_id,
            func.count(CareNote.id).label('count')
        )
        .where(and_(*filters))
        .group_by(CareNote.facility_id)
    )
    facility_results = (await db.execute(facility_query)).all()
    print(f"Facility results: {facility_results}")

    # Build stats
    stats = {
        "total_notes": total_result.total_notes or 0,
        "by_category": {row.category: row.count for row in category_results},
        "by_priority": {1: 0, 2: 0, 3: 0, 4: 0, 5: 0},
        "by_facility": {row.facility_id: row.count for row in facility_results},
        "avg_notes_per_patient": 0,
        "date_range": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat()
        }
    }

    # Update priority counts
    for row in priority_results:
        stats["by_priority"][row.priority] = row.count

    # Calculate average notes per patient
    if total_result.unique_patients and total_result.unique_patients > 0:
        stats["avg_notes_per_patient"] = round(stats["total_notes"] / total_result.unique_patients, 2)

    print(f"Returning stats: {stats}")
    return stats

