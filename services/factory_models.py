"""
factory_models — SQLAlchemy models for the Factory system.

Uses the shared Base from core.database. Import this module (or import
any of its classes) to register the tables with Base.metadata so they
are created automatically by create_all().
"""

from sqlalchemy import Column, String, Text, Integer, DateTime, ForeignKey, JSON, Index
from sqlalchemy.orm import relationship

from core.database import Base


class FactoryProject(Base):
    """A Factory project — a DAG of tasks orchestrated end-to-end."""
    __tablename__ = "factory_projects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner = Column(String, nullable=False, default="default", index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="draft")
    model = Column(String, nullable=True)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

    nodes = relationship("FactoryNode", back_populates="project",
                        cascade="all, delete-orphan", passive_deletes=True)
    events = relationship("FactoryEvent", back_populates="project",
                          cascade="all, delete-orphan", passive_deletes=True)

    __table_args__ = (
        Index("ix_factory_projects_owner_status", "owner", "status"),
    )


class FactoryNode(Base):
    """A single task within a Factory project."""
    __tablename__ = "factory_nodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("factory_projects.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    task_type = Column(String, nullable=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    assigned_agent = Column(String, nullable=True)
    filename = Column(String, nullable=True)
    dependencies = Column(JSON, nullable=True)
    status = Column(String, nullable=False, default="pending")
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    priority = Column(Integer, default=0)
    retries = Column(Integer, default=0)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

    project = relationship("FactoryProject", back_populates="nodes")
    edges_from = relationship("FactoryEdge", foreign_keys="FactoryEdge.from_node_id",
                              back_populates="from_node", cascade="all, delete-orphan")
    edges_to = relationship("FactoryEdge", foreign_keys="FactoryEdge.to_node_id",
                            back_populates="to_node", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_factory_nodes_project_status", "project_id", "status"),
    )


class FactoryEdge(Base):
    """A directed edge: from_node must complete before to_node can start."""
    __tablename__ = "factory_edges"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("factory_projects.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    from_node_id = Column(Integer, ForeignKey("factory_nodes.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    to_node_id = Column(Integer, ForeignKey("factory_nodes.id", ondelete="CASCADE"),
                        nullable=False, index=True)

    project = relationship("FactoryProject")
    from_node = relationship("FactoryNode", foreign_keys=[from_node_id], back_populates="edges_from")
    to_node = relationship("FactoryNode", foreign_keys=[to_node_id], back_populates="edges_to")

    __table_args__ = (
        Index("ix_factory_edges_project", "project_id"),
    )


class FactoryEvent(Base):
    """Timeline event for a Factory project (for audit/debug)."""
    __tablename__ = "factory_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("factory_projects.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    node_id = Column(Integer, ForeignKey("factory_nodes.id", ondelete="SET NULL"),
                     nullable=True, index=True)
    event_type = Column(String, nullable=False)
    message = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSON, nullable=True)
    created_at = Column(DateTime, nullable=False)

    project = relationship("FactoryProject", back_populates="events")


def init_factory_tables(engine) -> None:
    """Create factory tables if they don't exist.

    Called during app startup instead of relying on a circular import
    in core/database.py. Uses the shared Base.metadata so models are
    consistent, but only creates THIS module's tables (not all tables
    in metadata — avoids race with core's own create_all).
    """
    from sqlalchemy import inspect
    Base.metadata.create_all(
        engine,
        tables=[
            FactoryProject.__table__,
            FactoryNode.__table__,
            FactoryEdge.__table__,
            FactoryEvent.__table__,
        ],
    )


__all__ = ["FactoryProject", "FactoryNode", "FactoryEdge", "FactoryEvent", "init_factory_tables"]
