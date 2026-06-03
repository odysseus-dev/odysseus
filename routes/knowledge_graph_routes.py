from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from src.knowledge_graph import KnowledgeGraphManager

router = APIRouter(prefix="/api/knowledge-graph", tags=["knowledge-graph"])

_kg_manager_instance = None

def setup_knowledge_graph_routes(kg_manager: KnowledgeGraphManager = None):
    """Set up knowledge graph routes, optionally injecting a manager."""
    global _kg_manager_instance
    if kg_manager:
        _kg_manager_instance = kg_manager
    return router

def get_kg_manager() -> KnowledgeGraphManager:
    if _kg_manager_instance is None:
        raise HTTPException(status_code=503, detail="Knowledge graph manager not initialized")
    return _kg_manager_instance

# Node endpoints
@router.get("/nodes", response_model=List[dict])
async def list_nodes(
    node_type: Optional[str] = Query(None, description="Filter by node type"),
    owner: Optional[str] = Query(None, description="Filter by owner"),
    source_system: Optional[str] = Query(None, description="Filter by source system"),
    locked: Optional[bool] = Query(None, description="Filter by locked status"),
    limit: int = Query(100, description="Maximum number of nodes to return"),
    offset: int = Query(0, description="Offset for pagination"),
    kg_manager: KnowledgeGraphManager = Depends(get_kg_manager)
):
    """List knowledge graph nodes with optional filters."""
    nodes = kg_manager.list_nodes(
        node_type=node_type,
        owner=owner,
        source_system=source_system,
        locked=locked,
        limit=limit,
        offset=offset
    )
    
    # Convert to dictionaries for JSON response
    result = []
    for node in nodes:
        result.append({
            "id": node.id,
            "type": node.type,
            "owner": node.owner,
            "source_system": node.source_system,
            "source_path": node.source_path,
            "content": node.content,
            "node_metadata": node.node_metadata,
            "created_at": node.created_at.isoformat() if node.created_at else None,
            "updated_at": node.updated_at.isoformat() if node.updated_at else None,
            "imported_at": node.imported_at.isoformat() if node.imported_at else None,
            "locked": bool(node.locked)
        })
    return result

@router.get("/nodes/{node_id}", response_model=dict)
async def get_node(
    node_id: str,
    kg_manager: KnowledgeGraphManager = Depends(get_kg_manager)
):
    """Get a specific knowledge graph node by ID."""
    node = kg_manager.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    
    return {
        "id": node.id,
        "type": node.type,
        "owner": node.owner,
        "source_system": node.source_system,
        "source_path": node.source_path,
        "content": node.content,
        "node_metadata": node.node_metadata,
        "created_at": node.created_at.isoformat() if node.created_at else None,
        "updated_at": node.updated_at.isoformat() if node.updated_at else None,
        "imported_at": node.imported_at.isoformat() if node.imported_at else None,
        "locked": bool(node.locked)
    }

# Edge endpoints
@router.get("/edges", response_model=List[dict])
async def list_edges(
    edge_type: Optional[str] = Query(None, description="Filter by edge type"),
    owner: Optional[str] = Query(None, description="Filter by owner"),
    source_node_id: Optional[str] = Query(None, description="Filter by source node ID"),
    target_node_id: Optional[str] = Query(None, description="Filter by target node ID"),
    limit: int = Query(100, description="Maximum number of edges to return"),
    offset: int = Query(0, description="Offset for pagination"),
    kg_manager: KnowledgeGraphManager = Depends(get_kg_manager)
):
    """List knowledge graph edges with optional filters."""
    edges = kg_manager.list_edges(
        edge_type=edge_type,
        owner=owner,
        source_node_id=source_node_id,
        target_node_id=target_node_id,
        limit=limit,
        offset=offset
    )
    
    # Convert to dictionaries for JSON response
    result = []
    for edge in edges:
        result.append({
            "id": edge.id,
            "type": edge.type,
            "source_node_id": edge.source_node_id,
            "target_node_id": edge.target_node_id,
            "owner": edge.owner,
            "confidence": edge.confidence,
            "evidence": edge.evidence,
            "created_at": edge.created_at.isoformat() if edge.created_at else None,
            "updated_at": edge.updated_at.isoformat() if edge.updated_at else None
        })
    return result

@router.get("/edges/{edge_id}", response_model=dict)
async def get_edge(
    edge_id: str,
    kg_manager: KnowledgeGraphManager = Depends(get_kg_manager)
):
    """Get a specific knowledge graph edge by ID."""
    edge = kg_manager.get_edge(edge_id)
    if not edge:
        raise HTTPException(status_code=404, detail="Edge not found")
    
    return {
        "id": edge.id,
        "type": edge.type,
        "source_node_id": edge.source_node_id,
        "target_node_id": edge.target_node_id,
        "owner": edge.owner,
        "confidence": edge.confidence,
        "evidence": edge.evidence,
        "created_at": edge.created_at.isoformat() if edge.created_at else None,
        "updated_at": edge.updated_at.isoformat() if edge.updated_at else None
    }

# Context pack endpoint
@router.get("/context")
async def get_context_pack(
    query: str = Query(..., description="Query string for context generation"),
    owner: Optional[str] = Query(None, description="Owner for filtering context"),
    limit: int = Query(5, description="Maximum number of nodes to include in context"),
    kg_manager: KnowledgeGraphManager = Depends(get_kg_manager)
):
    """Generate a context pack for chat injection based on the knowledge graph."""
    context_pack = kg_manager.get_context_pack(query=query, owner=owner, limit=limit)
    return {"context": context_pack}

# Health check endpoint
@router.get("/health")
async def health_check():
    """Check if the knowledge graph service is healthy."""
    if _kg_manager_instance is None:
        raise HTTPException(status_code=503, detail="Knowledge graph manager not initialized")
    return {"status": "healthy", "service": "knowledge-graph"}