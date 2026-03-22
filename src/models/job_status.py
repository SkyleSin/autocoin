from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    Ready = "Ready"
    Running = "Running"
    Complete = "Complete"
    Error = "Error"
