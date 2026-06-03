import os
import logging
from datetime import datetime
from sqlalchemy import create_engine, Column, String, Text, Integer, DateTime, ForeignKey, Index
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker

logger = logging.getLogger(__name__)

# Knowledge Graph database URL - separate from main app database
KG_DATABASE_URL = os.getenv("KG_DATABASE_URL", "sqlite:///./data/knowledge-graph/kg.db")

# Create engine for knowledge graph
kg_engine = create_engine(
    KG_DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in KG_DATABASE_URL else {}
)

# Create session factory for knowledge graph
KgSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=kg_engine)

# Base class for knowledge graph models
KgBase = declarative_base()


class KgNode(KgBase):
    """
    Knowledge Graph Node entity.
    Represents a concept, memory, skill, document, etc.
    """
    __tablename__ = "kg_nodes"

    id = Column(String, primary_key=True, index=True)
    # Type of node: memory, skill, document, chat, verification, etc.
    type = Column(String, nullable=False, index=True)
    # Owner for multi-user isolation
    owner = Column(String, nullable=True, index=True)
    # Source system: infinite_brain, command_suite, patches, user, system
    source_system = Column(String, nullable=True)
    # Source path for provenance
    source_path = Column(String, nullable=True)
    # Content or summary of the node
    content = Column(Text, nullable=True)
    # Additional metadata as JSON string
    node_metadata = Column("metadata", Text, nullable=True)
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    # Imported at timestamp
    imported_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    # Locked flag for imported items that require admin review
    locked = Column(Integer, default=0, nullable=False)  # 0=false, 1=true

    # Relationships
    edges_out = relationship("KgEdge", foreign_keys="KgEdge.source_node_id", back_populates="source_node", cascade="all, delete-orphan")
    edges_in = relationship("KgEdge", foreign_keys="KgEdge.target_node_id", back_populates="target_node", cascade="all, delete-orphan")

    # Indexes for common queries
    __table_args__ = (
        Index('ix_kg_nodes_owner_type', 'owner', 'type'),
        Index('ix_kg_nodes_source_system', 'source_system'),
        Index('ix_kg_nodes_locked', 'locked'),
    )


class KgEdge(KgBase):
    """
    Knowledge Graph Edge entity.
    Represents a relationship between two nodes.
    """
    __tablename__ = "kg_edges"

    id = Column(String, primary_key=True, index=True)
    # Type of edge: derived_from, supersedes, conflicts_with, enables, requires, related_to, etc.
    type = Column(String, nullable=False, index=True)
    # Source node ID
    source_node_id = Column(String, ForeignKey("kg_nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    # Target node ID
    target_node_id = Column(String, ForeignKey("kg_nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    # Owner for multi-user isolation (should match source and target node owners)
    owner = Column(String, nullable=True, index=True)
    # Confidence score (0.0 to 1.0)
    confidence = Column(Integer, default=100, nullable=False)  # Store as integer percentage (0-100)
    # Evidence or reason for this edge
    evidence = Column(Text, nullable=True)
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    source_node = relationship("KgNode", foreign_keys=[source_node_id], back_populates="edges_out")
    target_node = relationship("KgNode", foreign_keys=[target_node_id], back_populates="edges_in")

    # Indexes for common queries
    __table_args__ = (
        Index('ix_kg_edges_owner_type', 'owner', 'type'),
        Index('ix_kg_edges_source_target', 'source_node_id', 'target_node_id'),
    )


class KnowledgeGraphManager:
    """
    Manages the knowledge graph database operations.
    Provides CRUD operations for nodes and edges, context pack generation,
    and synchronization with other Juniperus systems.
    """

    def __init__(self, database_url: str = None):
        if database_url:
            global kg_engine, KgSessionLocal
            kg_engine = create_engine(
                database_url,
                connect_args={"check_same_thread": False} if "sqlite" in database_url else {}
            )
            KgSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=kg_engine)
        KgBase.metadata.create_all(bind=kg_engine)
        logger.info("Knowledge Graph Manager initialized")

    def _session(self):
        """Get a new database session."""
        return KgSessionLocal()

    # Node CRUD operations
    def create_node(self, node_id: str, node_type: str, owner: str = None,
                   source_system: str = None, source_path: str = None,
                   content: str = None, metadata: dict = None, locked: bool = False):
        """Create a new node in the knowledge graph."""
        session = self._session()
        try:
            existing = session.query(KgNode).filter_by(id=node_id).first()
            if existing:
                logger.warning(f"Node {node_id} already exists, returning existing")
                return existing
            node = KgNode(
                id=node_id,
                type=node_type,
                owner=owner,
                source_system=source_system,
                source_path=source_path,
                content=content,
                node_metadata=str(metadata) if metadata else None,
                locked=1 if locked else 0
            )
            session.add(node)
            session.commit()
            session.refresh(node)
            logger.debug(f"Created node: {node_id} of type {node_type}")
            return node
        except Exception as e:
            session.rollback()
            logger.error(f"create_node failed: {e}")
            raise
        finally:
            session.close()

    def get_node(self, node_id: str):
        """Get a node by ID."""
        session = self._session()
        try:
            return session.query(KgNode).filter_by(id=node_id).first()
        finally:
            session.close()

    def update_node(self, node_id: str, **kwargs):
        """Update a node's attributes."""
        session = self._session()
        try:
            node = session.query(KgNode).filter_by(id=node_id).first()
            if not node:
                logger.warning(f"Node {node_id} not found for update")
                return None
            for key, value in kwargs.items():
                if hasattr(node, key):
                    if key == "node_metadata" and isinstance(value, dict):
                        setattr(node, key, str(value))
                    elif key == "locked":
                        setattr(node, key, 1 if value else 0)
                    else:
                        setattr(node, key, value)
            node.updated_at = datetime.utcnow()
            session.commit()
            session.refresh(node)
            return node
        except Exception as e:
            session.rollback()
            raise
        finally:
            session.close()

    def delete_node(self, node_id: str) -> bool:
        """Delete a node by ID."""
        session = self._session()
        try:
            node = session.query(KgNode).filter_by(id=node_id).first()
            if not node:
                return False
            session.delete(node)
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            raise
        finally:
            session.close()

    def list_nodes(self, node_type: str = None, owner: str = None,
                   source_system: str = None, locked: bool = None,
                   limit: int = 100, offset: int = 0) -> list:
        """List nodes with optional filters."""
        session = self._session()
        try:
            query = session.query(KgNode)
            if node_type:
                query = query.filter_by(type=node_type)
            if owner is not None:
                query = query.filter_by(owner=owner)
            if source_system:
                query = query.filter_by(source_system=source_system)
            if locked is not None:
                query = query.filter_by(locked=1 if locked else 0)
            return query.offset(offset).limit(limit).all()
        finally:
            session.close()

    # Edge CRUD operations
    def create_edge(self, edge_id: str, edge_type: str, source_node_id: str,
                   target_node_id: str, owner: str = None, confidence: int = 100,
                   evidence: str = None):
        """Create a new edge in the knowledge graph."""
        session = self._session()
        try:
            existing = session.query(KgEdge).filter_by(id=edge_id).first()
            if existing:
                return existing
            source_node = session.query(KgNode).filter_by(id=source_node_id).first()
            if not source_node:
                logger.warning(f"Source node {source_node_id} not found")
                return None
            target_node = session.query(KgNode).filter_by(id=target_node_id).first()
            if not target_node:
                logger.warning(f"Target node {target_node_id} not found")
                return None
            edge = KgEdge(
                id=edge_id,
                type=edge_type,
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                owner=owner,
                confidence=confidence,
                evidence=evidence
            )
            session.add(edge)
            session.commit()
            session.refresh(edge)
            return edge
        except Exception as e:
            session.rollback()
            raise
        finally:
            session.close()

    def get_edge(self, edge_id: str):
        """Get an edge by ID."""
        session = self._session()
        try:
            return session.query(KgEdge).filter_by(id=edge_id).first()
        finally:
            session.close()

    def update_edge(self, edge_id: str, **kwargs):
        """Update an edge's attributes."""
        session = self._session()
        try:
            edge = session.query(KgEdge).filter_by(id=edge_id).first()
            if not edge:
                return None
            for key, value in kwargs.items():
                if hasattr(edge, key):
                    if key == "confidence":
                        value = max(0, min(100, value))
                    setattr(edge, key, value)
            edge.updated_at = datetime.utcnow()
            session.commit()
            session.refresh(edge)
            return edge
        except Exception as e:
            session.rollback()
            raise
        finally:
            session.close()

    def delete_edge(self, edge_id: str) -> bool:
        """Delete an edge by ID."""
        session = self._session()
        try:
            edge = session.query(KgEdge).filter_by(id=edge_id).first()
            if not edge:
                return False
            session.delete(edge)
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            raise
        finally:
            session.close()

    def list_edges(self, edge_type: str = None, owner: str = None,
                   source_node_id: str = None, target_node_id: str = None,
                   limit: int = 100, offset: int = 0) -> list:
        """List edges with optional filters."""
        session = self._session()
        try:
            query = session.query(KgEdge)
            if edge_type:
                query = query.filter_by(type=edge_type)
            if owner is not None:
                query = query.filter_by(owner=owner)
            if source_node_id:
                query = query.filter_by(source_node_id=source_node_id)
            if target_node_id:
                query = query.filter_by(target_node_id=target_node_id)
            return query.offset(offset).limit(limit).all()
        finally:
            session.close()

    # Context pack generation for chat injection
    def get_context_pack(self, query: str, owner: str = None, limit: int = 5) -> str:
        """Generate a context pack string for chat injection."""
        session = self._session()
        try:
            query_obj = session.query(KgNode)
            if owner is not None:
                query_obj = query_obj.filter_by(owner=owner)
            nodes = query_obj.order_by(KgNode.updated_at.desc()).limit(limit).all()
            if not nodes:
                return ""
            context_parts = ["Knowledge Graph Context:"]
            for node in nodes:
                context_parts.append(f"- [{node.type}] {node.content or node.id}")
            return "\n".join(context_parts)
        finally:
            session.close()

    # Synchronization methods
    def upsert_memory_node(self, memory_entry: dict, owner: str = None):
        import hashlib
        memory_id = f"mem_{hashlib.md5(str(memory_entry).encode()).hexdigest()}"
        node = self.get_node(memory_id)
        if node:
            return self.update_node(
                memory_id,
                content=memory_entry.get("text"),
                node_metadata={
                    "category": memory_entry.get("category"),
                    "source": memory_entry.get("source"),
                    "timestamp": memory_entry.get("timestamp")
                }
            )
        return self.create_node(
            node_id=memory_id,
            node_type="memory",
            owner=owner,
            source_system="user",
            content=memory_entry.get("text"),
            metadata={
                "category": memory_entry.get("category"),
                "source": memory_entry.get("source"),
                "timestamp": memory_entry.get("timestamp")
            }
        )

    def upsert_skill_node(self, skill_data: dict, owner: str = None):
        import hashlib
        skill_id = f"skill_{hashlib.md5(str(skill_data).encode()).hexdigest()}"
        node = self.get_node(skill_id)
        if node:
            return self.update_node(
                skill_id,
                content=skill_data.get("description"),
                node_metadata={
                    "name": skill_data.get("name"),
                    "category": skill_data.get("category"),
                    "confidence": skill_data.get("confidence")
                }
            )
        return self.create_node(
            node_id=skill_id,
            node_type="skill",
            owner=owner,
            source_system="user",
            content=skill_data.get("description"),
            metadata={
                "name": skill_data.get("name"),
                "category": skill_data.get("category"),
                "confidence": skill_data.get("confidence")
            }
        )

    def upsert_document_node(self, doc_metadata: dict, owner: str = None):
        import hashlib
        doc_id = f"doc_{hashlib.md5(str(doc_metadata).encode()).hexdigest()}"
        node = self.get_node(doc_id)
        if node:
            return self.update_node(
                doc_id,
                content=doc_metadata.get("title") or doc_metadata.get("filename") or doc_metadata.get("source"),
                node_metadata=doc_metadata
            )
        return self.create_node(
            node_id=doc_id,
            node_type="document",
            owner=owner,
            source_system="user",
            content=doc_metadata.get("title") or doc_metadata.get("filename") or doc_metadata.get("source"),
            metadata=doc_metadata
        )


knowledge_graph_manager = None