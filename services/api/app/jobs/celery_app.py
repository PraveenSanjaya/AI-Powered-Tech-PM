from celery import Celery

from app.core.config import get_settings
from app.core.db import session_factory
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)
celery = Celery("techpm", broker=settings.redis_url, backend=settings.redis_url)
celery.conf.update(
    task_soft_time_limit=120, task_time_limit=150, task_acks_late=True, worker_prefetch_multiplier=1,
    timezone="UTC", broker_connection_retry_on_startup=True,
    beat_schedule={
        "scheduler-tick": {"task": "techpm.tick", "schedule": 60.0},
        "periodic-sync": {"task": "techpm.sync_all", "schedule": 900.0},
    },
)


@celery.task(name="techpm.tick")
def tick_task() -> list[int]:
    from app.jobs.schedules import tick
    with session_factory()() as db:
        return tick(db)


@celery.task(name="techpm.sync_all")
def sync_all_task() -> int:
    from sqlalchemy import select
    from app.models import IntegrationConnection, Project
    from app.services.sync import run_sync
    n = 0
    with session_factory()() as db:
        for conn in db.scalars(select(IntegrationConnection).where(IntegrationConnection.status == "connected")):
            project = db.scalar(select(Project).where(Project.workspace_id == conn.workspace_id))
            if project:
                run_sync(db, conn, project)
                db.commit()
                n += 1
    return n
