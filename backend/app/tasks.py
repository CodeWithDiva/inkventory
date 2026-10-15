"""Background job dispatch and the periodic scheduler.

TASK_MODE=celery  -> jobs go to Redis and are processed by `celery -A app.tasks worker -B`
                     (-B runs the beat scheduler for periodic tasks such as scheduled store syncs)
TASK_MODE=thread  -> jobs run in a background thread of the API process (local dev)
TASK_MODE=sync    -> jobs run inside the request (tests)
"""

import logging
import threading

from celery import Celery

from app.config import get_settings

settings = get_settings()
log = logging.getLogger(__name__)
SCHEDULER_INTERVAL_SECONDS = 300

celery_app = Celery("catalogai", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    beat_schedule={"due-store-syncs": {"task": "sync.run_due", "schedule": SCHEDULER_INTERVAL_SECONDS}},
)


@celery_app.task(name="generation.run_job")
def run_generation_job_task(job_id: int) -> None:
    from app.services.generation import run_job

    run_job(job_id)


@celery_app.task(name="export.run_export")
def run_export_task(export_id: int) -> None:
    from app.services.exporters import run_export

    run_export(export_id)


@celery_app.task(name="sync.run_sync")
def run_sync_task(sync_id: int) -> None:
    from app.services.store_sync import run_sync

    run_sync(sync_id)


@celery_app.task(name="sync.run_due")
def run_due_task() -> int:
    from app.services.store_sync import run_due_syncs

    return run_due_syncs()


def _dispatch(task, func_path: str, obj_id: int) -> None:
    mode = get_settings().task_mode
    if mode == "celery":
        task.delay(obj_id)
        return
    module_name, func_name = func_path.rsplit(".", 1)
    module = __import__(module_name, fromlist=[func_name])
    func = getattr(module, func_name)
    if mode == "sync":
        func(obj_id)
    else:
        threading.Thread(target=func, args=(obj_id,), daemon=True).start()


def dispatch_generation(job_id: int) -> None:
    _dispatch(run_generation_job_task, "app.services.generation.run_job", job_id)


def dispatch_export(export_id: int) -> None:
    _dispatch(run_export_task, "app.services.exporters.run_export", export_id)


def dispatch_sync(sync_id: int) -> None:
    _dispatch(run_sync_task, "app.services.store_sync.run_sync", sync_id)


def start_thread_scheduler(stop: threading.Event) -> threading.Thread | None:
    """In TASK_MODE=thread, run periodic work in the API process (Celery beat does it in production)."""
    if get_settings().task_mode != "thread":
        return None

    def loop() -> None:
        from app.services.store_sync import run_due_syncs

        while not stop.wait(SCHEDULER_INTERVAL_SECONDS):
            try:
                run_due_syncs()
            except Exception:
                log.exception("Scheduled sync check failed")

    thread = threading.Thread(target=loop, name="scheduler", daemon=True)
    thread.start()
    return thread
