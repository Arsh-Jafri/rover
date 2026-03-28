web: uvicorn rover.api:app --host 0.0.0.0 --port $PORT
worker: celery -A rover.celery_app worker --loglevel=info --concurrency=4
beat: celery -A rover.celery_app beat --loglevel=info
