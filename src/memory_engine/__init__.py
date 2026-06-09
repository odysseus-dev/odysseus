"""Hierarchical memory engine for Odysseus.

Provides TRACE-inspired episodic memory, structured profile memory,
and an enhanced provider that unifies them with the existing flat fact store.
"""

from .episodic_tree import EpisodicTree, TopicNode, MessageNode
from .profile_manager import ProfileManager, ProfileEntry
from .topic_classifier import TopicClassifier, HeuristicClassifier
from .prompt_synthesizer import PromptSynthesizer
from .tree_reorganizer import TreeReorganizer
from .enhanced_provider import EnhancedMemoryProvider

__all__ = [
    "EpisodicTree",
    "TopicNode",
    "MessageNode",
    "ProfileManager",
    "ProfileEntry",
    "TopicClassifier",
    "HeuristicClassifier",
    "PromptSynthesizer",
    "TreeReorganizer",
    "EnhancedMemoryProvider",
]
