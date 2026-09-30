"""Dedicated Celery process entry point. Importing this module performs no I/O."""

import logging
import sys

from celery import signals

from fleetlink.core.config import Settings
from fleetlink.core.logging import JsonFormatter, configure_logging
from fleetlink.infrastructure.broker import create_celery


class WorkerFormatter(JsonFormatter):
    def format(self, record: logging.LogRecord) -> str:
        # Library messages can contain broker URLs, exception text and untrusted payloads.
        safe = logging.makeLogRecord(record.__dict__.copy())
        if not record.name.startswith("fleetlink."):
            safe.msg = "worker_runtime_event"
            safe.args = ()
            if record.exc_info is not None and record.exc_info[1] is not None:
                safe.error_type = type(record.exc_info[1]).__name__
        safe.exc_info = None
        safe.exc_text = None
        safe.stack_info = None
        return (
            super()
            .format(safe)
            .replace('"service": "fleetlink-api"', '"service": "fleetlink-worker"')
        )


def configure_worker_logging(**kwargs: object) -> None:
    configure_logging()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(WorkerFormatter())
    for name in ("", "fleetlink", "celery", "celery.task", "celery.worker", "kombu", "amqp"):
        logger = logging.getLogger(name)
        logger.handlers = [handler] if name in ("", "fleetlink") else []
        logger.propagate = name != "fleetlink"
        logger.setLevel(logging.INFO)


def main() -> None:
    settings = Settings()
    app = create_celery(settings)
    signals.setup_logging.connect(configure_worker_logging, weak=False)
    try:
        worker = app.Worker(
            pool="prefork",
            concurrency=settings.celery_concurrency,
            quiet=True,
            without_gossip=True,
            without_mingle=True,
            without_heartbeat=True,
            loglevel=settings.log_level,
        )
        worker.start()
        if worker.exitcode:
            raise SystemExit(worker.exitcode)
    finally:
        app.close()


if __name__ == "__main__":
    main()
