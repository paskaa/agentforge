"""
Dead Letter Queue — failed task handling with retry and replay.

Failed tasks are moved to a separate Redis Stream after
max_retries attempts. They can be replayed (moved back to
the main queue) or inspected.

DLQ stream: agent-work-dlq
Fields added: _retries, _last_error, _original_stream
"""

import json
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger("agentforge.dlq")


class DeadLetterQueue:
    """Redis-backed dead letter queue for failed tasks."""

    DLQ_STREAM = "agent-work-dlq"
    MAX_RETRIES = 3

    def __init__(self, redis_client, source_stream: str = "agent-work-queue"):
        self.redis = redis_client
        self.source_stream = source_stream

    def record_failure(self, task: dict[str, Any], error: str) -> bool:
        """Move a failed task to the DLQ. Returns True if moved."""
        retries = int(task.get("_retries", 0)) + 1
        if retries <= self.MAX_RETRIES:
            # Still retrying — keep in source stream (will be picked up as pending)
            logger.info("Task %s retry %d/%d", task.get("msg_id", "?"), retries, self.MAX_RETRIES)
            return False

        # Move to DLQ
        fields = {
            **task,
            "_retries": str(retries),
            "_last_error": error[:500],
            "_original_stream": self.source_stream,
            "_dlq_time": datetime.now().isoformat(),
        }
        self.redis.xadd(self.DLQ_STREAM, fields)
        logger.warning("Task %s moved to DLQ after %d retries: %s",
                       task.get("msg_id", "?"), retries, error[:100])
        return True

    def list_dlq(self, count: int = 20) -> list[dict]:
        """List dead-lettered tasks."""
        try:
            result = self.redis.xrevrange(self.DLQ_STREAM, count=count)
            tasks = []
            for msg_id, fields in result:
                tasks.append({"dlq_id": msg_id, **fields})
            return tasks
        except Exception:
            return []

    def replay(self, dlq_id: str) -> bool:
        """Replay a dead-lettered task back to the source stream."""
        try:
            # Read the DLQ entry
            result = self.redis.xrange(self.DLQ_STREAM, min=dlq_id, max=dlq_id, count=1)
            if not result:
                return False
            _, fields = result[0]

            # Remove DLQ metadata
            fields.pop("_retries", None)
            fields.pop("_last_error", None)
            fields.pop("_original_stream", None)
            fields.pop("_dlq_time", None)

            # Republish to source stream
            self.redis.xadd(self.source_stream, fields)

            # Delete from DLQ
            self.redis.xdel(self.DLQ_STREAM, dlq_id)
            logger.info("Replayed DLQ task %s to %s", dlq_id, self.source_stream)
            return True
        except Exception as e:
            logger.error("Failed to replay DLQ task %s: %s", dlq_id, e)
            return False

    def purge(self, dlq_id: str) -> bool:
        """Permanently delete a dead-lettered task."""
        try:
            self.redis.xdel(self.DLQ_STREAM, dlq_id)
            return True
        except Exception:
            return False

    def size(self) -> int:
        """Number of tasks in the DLQ."""
        try:
            return self.redis.xlen(self.DLQ_STREAM) or 0
        except Exception:
            return 0
