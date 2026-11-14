"""Synchronization for research report ownership and persistence.

Protects operations within one Python process.
Multiple workers require cross-process coordination.
"""

from threading import RLock

research_report_lock = RLock()
