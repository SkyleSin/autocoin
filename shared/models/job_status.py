from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    Ready = "Ready"
    Queue = "Queue"
    Running = "Running"
    Complete = "Complete"
    Failed = "Failed"
    Error = "Error"
