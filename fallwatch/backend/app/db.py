"""Database tables: one row per fall event, one row per stored image, plus the hash-chain head."""
from datetime import datetime, timezone

from sqlalchemy import (BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint, create_engine)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from . import config


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("device_id", "client_event_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64), index=True)
    client_event_id: Mapped[str] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    detected_at_ms: Mapped[int] = mapped_column(BigInteger)       # phone clock
    stand_t_ms: Mapped[int] = mapped_column(BigInteger)           # key frame 1 time
    lie_t_ms: Mapped[int] = mapped_column(BigInteger)             # key frame 2 time
    descent_ms: Mapped[int] = mapped_column(Integer)
    hip_drop: Mapped[float] = mapped_column(Float)                # body heights
    hip_speed: Mapped[float] = mapped_column(Float)               # body heights per second
    landing_side: Mapped[str] = mapped_column(String(16))         # left / right / unclear (estimate)
    floor_ms: Mapped[int] = mapped_column(Integer)
    recovered: Mapped[bool] = mapped_column(Boolean, default=False)
    n_images: Mapped[int] = mapped_column(Integer)
    meta_json: Mapped[str] = mapped_column(Text)                  # everything the phone sent
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    prev_chain_hash: Mapped[str] = mapped_column(String(64))
    chain_hash: Mapped[str] = mapped_column(String(64), unique=True)
    seq: Mapped[int] = mapped_column(Integer, unique=True)        # position in the chain

    images: Mapped[list["Image"]] = relationship(back_populates="event", order_by="Image.captured_at_ms")


class Image(Base):
    __tablename__ = "images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16))   # standing | laid | pre | burst | post
    captured_at_ms: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    bytes: Mapped[int] = mapped_column(Integer)
    object_key: Mapped[str] = mapped_column(String(255))

    event: Mapped[Event] = relationship(back_populates="images")


class Snapshot(Base):
    """Optional low-rate heartbeat pictures (only when HEARTBEAT_SEC > 0)."""
    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(64), index=True)
    captured_at_ms: Mapped[int] = mapped_column(BigInteger)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    posture: Mapped[str] = mapped_column(String(16))
    sha256: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(String(255))


class ChainHead(Base):
    """Single row holding the latest chain hash; locked while an event is appended."""
    __tablename__ = "chain_head"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    chain_hash: Mapped[str] = mapped_column(String(64), default="0" * 64)


def make_engine(url: str = None):
    url = url or config.DATABASE_URL
    if url.startswith("sqlite:///"):
        from pathlib import Path
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db(eng=None) -> None:
    eng = eng or engine
    Base.metadata.create_all(eng)
    with sessionmaker(bind=eng)() as s:
        if s.get(ChainHead, 1) is None:
            s.add(ChainHead(id=1, seq=0, chain_hash="0" * 64))
            s.commit()
