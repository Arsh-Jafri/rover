import os

from dotenv import load_dotenv

load_dotenv()

from celery import Celery

redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

app = Celery("rover", broker=redis_url, backend=redis_url)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

app.conf.beat_schedule = {
    "dispatch-email-scan": {
        "task": "rover.tasks.dispatch_email_scan",
        "schedule": 30 * 60,  # 30 minutes
    },
    "dispatch-price-check": {
        "task": "rover.tasks.dispatch_price_check",
        "schedule": 6 * 60 * 60,  # 6 hours
    },
    "dispatch-claims": {
        "task": "rover.tasks.dispatch_claims",
        "schedule": 24 * 60 * 60,  # 24 hours
    },
}

app.autodiscover_tasks(["rover"])
