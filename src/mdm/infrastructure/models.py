from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, Boolean, DateTime, Integer, Float, ForeignKey, JSON, Index
from sqlalchemy.orm import relationship
from src.mdm.infrastructure.database import MDMBase


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MDMDevice(MDMBase):
    __tablename__ = "mdm_devices"
    id = Column(String, primary_key=True, index=True)
    udid = Column(String(64), unique=True, nullable=False, index=True)
    modele = Column(String(128), nullable=False)
    ios_version = Column(String(16), nullable=False)
    capacite_go = Column(Integer, nullable=False)
    stockage_utilise_go = Column(Float, default=0)
    proprietaire = Column(String(128), nullable=True)
    type_appareil = Column(String(32), default="iPad")
    notes = Column(Text, nullable=True)
    est_actif = Column(Boolean, default=True)
    derniere_sync = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    enrollments = relationship("MDMEnrollment", back_populates="device", cascade="all, delete-orphan")
    matchs = relationship("MDMMatchResult", back_populates="device", cascade="all, delete-orphan")
    __table_args__ = (Index("ix_mdm_device_actif", "est_actif", "type_appareil"),)


class MDMProfile(MDMBase):
    __tablename__ = "mdm_profiles"
    id = Column(String, primary_key=True, index=True)
    nom = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    payload_json = Column(JSON, default=dict)
    ios_min_version = Column(String(16), nullable=True)
    categorie = Column(String(64), nullable=True)
    est_actif = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    attributes = relationship("MDMProfileAttribute", back_populates="profile", cascade="all, delete-orphan")
    enrollments = relationship("MDMEnrollment", back_populates="profile", cascade="all, delete-orphan")


class MDMProfileAttribute(MDMBase):
    __tablename__ = "mdm_profile_attributes"
    id = Column(String, primary_key=True, index=True)
    profile_id = Column(String, ForeignKey("mdm_profiles.id"), nullable=False, index=True)
    cle = Column(String(64), nullable=False)
    valeur = Column(Text, nullable=False)
    profile = relationship("MDMProfile", back_populates="attributes")


class MDMEnrollment(MDMBase):
    __tablename__ = "mdm_enrollments"
    id = Column(String, primary_key=True, index=True)
    device_id = Column(String, ForeignKey("mdm_devices.id"), nullable=False, index=True)
    profile_id = Column(String, ForeignKey("mdm_profiles.id"), nullable=False, index=True)
    statut = Column(String(32), default="pending")
    applied_at = Column(DateTime, nullable=True)
    device = relationship("MDMDevice", back_populates="enrollments")
    profile = relationship("MDMProfile", back_populates="enrollments")
    __table_args__ = (Index("ix_mdm_enroll_statut", "statut", "device_id"),)


class MDMCapacityLog(MDMBase):
    __tablename__ = "mdm_capacity_logs"
    id = Column(String, primary_key=True, index=True)
    total_devices = Column(Integer, default=0)
    total_capacite_go = Column(Float, default=0)
    total_utilise_go = Column(Float, default=0)
    moyenne_ios = Column(String(8), nullable=True)
    logged_at = Column(DateTime, default=_utcnow, index=True)


class MDMMatchResult(MDMBase):
    __tablename__ = "mdm_match_results"
    id = Column(String, primary_key=True, index=True)
    device_id = Column(String, ForeignKey("mdm_devices.id"), nullable=False, index=True)
    profile_id = Column(String, ForeignKey("mdm_profiles.id"), nullable=False, index=True)
    score = Column(Float, default=0)
    strategy = Column(String(32), default="cantique")
    matched_at = Column(DateTime, default=_utcnow)
    est_applique = Column(Boolean, default=False)
    device = relationship("MDMDevice", back_populates="matchs")
