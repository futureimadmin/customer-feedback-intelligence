"""
Heartbeat worker — pulls cfi-heartbeat-tick and starts HEARTBEAT pipeline runs.

Cloud Scheduler → Pub/Sub cfi-heartbeat-tick → this worker → POST /v1/scheduler/heartbeat-tick
(or inline create_run).

Env:
  GCP_PROJECT=intelligent-machines
  PUBSUB_HEARTBEAT_SUBSCRIPTION=cfi-heartbeat-tick-sub
  CONTROL_PLANE_URL=http://localhost:8080  (or in-process import)

Usage:
  python heartbeat_worker.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("heartbeat-worker")

PROJECT = os.environ.get("GCP_PROJECT", "intelligent-machines")
SUBSCRIPTION = os.environ.get("PUBSUB_HEARTBEAT_SUBSCRIPTION", "cfi-heartbeat-tick-sub")
CONTROL_PLANE_URL = os.environ.get("CONTROL_PLANE_URL", "http://localhost:8080").rstrip("/")


def trigger_heartbeat_tick() -> dict[str, Any]:
    url = f"{CONTROL_PLANE_URL}/v1/scheduler/heartbeat-tick"
    with httpx.Client(timeout=60.0) as client:
        r = client.post(url)
        r.raise_for_status()
        return r.json()


def pull_loop() -> None:
    from google.cloud import pubsub_v1

    subscriber = pubsub_v1.SubscriberClient()
    sub_path = subscriber.subscription_path(PROJECT, SUBSCRIPTION)
    logger.info("listening on %s", sub_path)

    def callback(message: Any) -> None:
        try:
            raw = message.data.decode("utf-8") if message.data else "{}"
            data = json.loads(raw) if raw else {}
            logger.info("heartbeat tick received: %s", data)
            result = trigger_heartbeat_tick()
            logger.info("heartbeat tick result: %s", result)
            message.ack()
        except Exception as e:
            logger.exception("heartbeat tick failed: %s", e)
            message.nack()

    fut = subscriber.subscribe(sub_path, callback=callback)
    try:
        fut.result()
    except KeyboardInterrupt:
        fut.cancel()


if __name__ == "__main__":
    if os.environ.get("HEARTBEAT_WORKER_MODE") == "once":
        print(json.dumps(trigger_heartbeat_tick(), indent=2))
        sys.exit(0)
    try:
        pull_loop()
    except Exception as e:
        logger.warning("Pub/Sub unavailable (%s). Retry in 60s loop.", e)
        while True:
            try:
                print(json.dumps(trigger_heartbeat_tick(), indent=2))
            except Exception as ex:
                logger.error("%s", ex)
            time.sleep(int(os.environ.get("HEARTBEAT_POLL_SECONDS", "300")))
