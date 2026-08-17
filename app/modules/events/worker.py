"""
Transactional Outbox Worker.

Reads unprocessed events from outbox_events table and publishes them.
In production, this would publish to a message broker (RabbitMQ, Kafka, etc.).
For now, it simply marks events as processed.
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.database import async_session_factory
from app.models import OutboxEvent

logger = logging.getLogger(__name__)


async def process_outbox_events(batch_size: int = 100) -> int:
    """Process a batch of unprocessed outbox events. Returns count of processed events."""
    processed = 0

    async with async_session_factory() as session:
        result = await session.execute(
            select(OutboxEvent)
            .where(OutboxEvent.processed == False)
            .order_by(OutboxEvent.occurred_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        events = result.scalars().all()

        for event in events:
            try:
                # In production: publish to message broker here
                # await publish_to_broker(event)

                logger.info(
                    f"Processing event: {event.event_type} "
                    f"for user {event.user_id} "
                    f"(event_id={event.id})"
                )

                event.processed = True
                event.processed_at = datetime.now(timezone.utc)
                processed += 1

            except Exception as e:
                logger.error(f"Failed to process event {event.id}: {e}")

        await session.commit()

    return processed


async def outbox_worker_loop(interval: int = 5):
    """Continuously process outbox events."""
    logger.info("Outbox worker started")
    while True:
        try:
            count = await process_outbox_events()
            if count > 0:
                logger.info(f"Processed {count} outbox events")
        except Exception as e:
            logger.error(f"Outbox worker error: {e}")

        await asyncio.sleep(interval)