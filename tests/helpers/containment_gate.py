"""Skip gate for tests that need bubblewrap-grade containment enforcement.

The python/bash/worker execution paths refuse to run at all —
``ContainmentUnavailable (filesystem, process_tree)`` — when the host cannot
establish the required containment dimensions. That is the designed behavior
on hosts without bubblewrap/unprivileged user namespaces (stock macOS), but it
means tests that drive the *enforcing* path cannot observe anything there.
Gate those tests with :func:`pytest.mark.skipif(not enforcing_containment_available())`
instead of asserting against an error the host can never avoid.

The probe is cached: the mechanism table is fixed for the life of the process,
and an uncached condition would run one subprocess probe per collected test.
"""

import functools

from src import containment


@functools.lru_cache(maxsize=1)
def enforcing_containment_available() -> bool:
    """True when this host can enforce every required containment dimension."""
    probe = containment.probe(
        containment.agent_spec(workspace="/tmp", env={}, wall_clock_s=5)
    )
    return probe.contained
