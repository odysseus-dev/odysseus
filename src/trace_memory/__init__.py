"""
TRACE — Temporal Retrieval And Context Engine
==============================================
A self-healing, hierarchical memory engine for long-running LLM agents.

Public surface:
    CTree          — the hierarchical B+Tree conversation store
    TopicNode      — a topic branch node in the tree
    MessageNode    — a leaf node holding a single user/assistant exchange
    Node           — base node class
    VectorDatabase — local SQLite vector store for semantic RAG
    ConversationVector — a conversation message with its embedding
    PromptSynthesizer  — assembles the full RAG-enriched system prompt

Quick start:
    from trace_memory import CTree, VectorDatabase, PromptSynthesizer

    tree = CTree(api_key="...", model="gpt-4o-mini")
    vdb  = VectorDatabase("memory.db")
    tree.vdb = vdb
    tree.synthesizer = PromptSynthesizer(tree, vdb)
"""

from .ctree import CTree, Node, TopicNode, MessageNode
from .vector_db import VectorDatabase, ConversationVector
from .prompt_synthesizer import PromptSynthesizer

__version__ = "1.0.0"
__author__  = "Husain"
__license__ = "MIT"

__all__ = [
    # Tree
    "CTree",
    "Node",
    "TopicNode",
    "MessageNode",
    # Vector store
    "VectorDatabase",
    "ConversationVector",
    # Prompt synthesis
    "PromptSynthesizer",
]
