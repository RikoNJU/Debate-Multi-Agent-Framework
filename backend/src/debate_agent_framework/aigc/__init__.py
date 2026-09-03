"""Standalone AIGC risk-screening feature."""

from .detector import AigcDetectorConfig, HuggingFaceAigcDetector
from .preprocessing import AigcWindowBuilder
from .repository import AigcTaskRepository
from .schemas import (
    AigcRiskLevel,
    AigcSegment,
    AigcSegmentResult,
    AigcTaskCreated,
    AigcTaskSnapshot,
    AigcTaskStatus,
)
from .service import AigcDetectionService

__all__ = [
    "AigcDetectionService",
    "AigcDetectorConfig",
    "AigcRiskLevel",
    "AigcSegment",
    "AigcSegmentResult",
    "AigcTaskCreated",
    "AigcTaskRepository",
    "AigcTaskSnapshot",
    "AigcTaskStatus",
    "AigcWindowBuilder",
    "HuggingFaceAigcDetector",
]
