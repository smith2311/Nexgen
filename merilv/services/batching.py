"""Small helpers for chunked database writes."""

from __future__ import annotations


def bulk_create_in_chunks(manager, objects, batch_size: int = 1000):
    """Bulk create rows without materialising the whole payload at once."""
    batch = []
    for obj in objects:
        batch.append(obj)
        if len(batch) >= batch_size:
            manager.bulk_create(batch, batch_size=batch_size)
            batch.clear()
    if batch:
        manager.bulk_create(batch, batch_size=batch_size)
