"""Explicit producer smoke command; completion is independently consumed from RabbitMQ."""

import json
from uuid import uuid4

from fleetlink.core.config import Settings
from fleetlink.infrastructure.broker import TechnicalProducer
from fleetlink.infrastructure.tasks import ProbePayload


def main() -> None:
    with TechnicalProducer(Settings()) as producer:
        result = producer.publish(ProbePayload(probe_id=str(uuid4()), value=21))
        print(json.dumps({"event": "technical_publish_confirmed", "task_id": result.id}))
        completed = producer.wait(result)
        print(json.dumps({"event": "technical_completion_observed", "result": completed}))


if __name__ == "__main__":
    main()
