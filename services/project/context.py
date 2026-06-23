"""ProjectContext — bundles per-project service handles.

Constructed once per project open (lazily, by ``ProjectService.open_context``).
For Shared mode the memory service is an alias to the global brain — no
per-project handles exist for memory. For Inherit/Isolated the project
gets its own ``MemoryManager`` and a ``MemoryVectorStore`` bound to a
per-project ChromaDB collection (``project_memory_<pid>``).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from src.memory import MemoryManager
from src.memory_vector import MemoryVectorStore
from services.memory.service import MemoryService


@dataclass
class ProjectContext:
    project_id: str
    owner: str
    data_dir: str
    memory_mode: str
    global_memory_service: Optional[MemoryService]

    memory_manager: Optional[MemoryManager] = None
    memory_vector: Optional[MemoryVectorStore] = None
    memory_service: Optional[MemoryService] = None
    rag: Optional["ProjectRagAdapter"] = None
    resource_store: Optional["ProjectResourceStore"] = None

    def __post_init__(self) -> None:
        # ResourceStore + RAG adapter exist for every mode (Shared projects
        # can still upload resources; they just alias the global memory).
        from services.project.rag import ProjectRagAdapter
        from services.project.resources import ProjectResourceStore
        self.rag = ProjectRagAdapter(project_id=self.project_id)
        self.resource_store = ProjectResourceStore(
            project_id=self.project_id, data_dir=self.data_dir, rag=self.rag,
        )

        if self.memory_mode == "shared":
            # Alias to the global brain. No per-project memory files.
            self.memory_service = self.global_memory_service
            return

        # Inherit / Isolated: per-project handles.
        os.makedirs(self.data_dir, exist_ok=True)
        self.memory_manager = MemoryManager(self.data_dir)
        collection = f"project_memory_{self.project_id}"
        self.memory_vector = MemoryVectorStore(
            data_dir=self.data_dir, collection_name=collection,
        )
        self.memory_service = MemoryService(
            data_dir=self.data_dir, vector_store=self.memory_vector,
        )

    @property
    def is_shared(self) -> bool:
        return self.memory_mode == "shared"
