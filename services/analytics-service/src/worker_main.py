"""Dedicated worker entrypoint for analytics jobs."""

import asyncio

import structlog

from src.config.logging_config import configure_logging
from src.config.settings import get_settings
from src.workers.job_queue import InMemoryJobQueue, RedisJobQueue
from src.workers.job_worker import JobWorker

logger = structlog.get_logger()


async def _run_worker() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("analytics_worker_starting")

    if settings.queue_backend == "redis":
        queue = RedisJobQueue(
            redis_url=settings.redis_url,
            stream_name=settings.redis_stream_name,
            dead_letter_stream=settings.redis_dead_letter_stream,
            consumer_group=settings.redis_consumer_group,
            consumer_name=settings.redis_consumer_name,
        )
    else:
        queue = InMemoryJobQueue()

    worker = JobWorker(queue, max_concurrent=settings.max_concurrent_jobs)
    await worker.start()


def main() -> None:
    asyncio.run(_run_worker())


if __name__ == "__main__":
    main()
