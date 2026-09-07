"""Preproduction initializer, run separately from the DML-only application."""
import time

from backend.config import get_settings
from backend.storage import client

for attempt in range(20):
    try:
        storage = client()
        buckets = [b["Name"] for b in storage.list_buckets()["Buckets"]]
        if get_settings().s3_bucket not in buckets:
            storage.create_bucket(Bucket=get_settings().s3_bucket)
        break
    except Exception:
        if attempt == 19:
            raise
        time.sleep(2)
