"""Compatibility exports for current-process metrics."""
from __future__ import annotations

from backend.winapi.self_process import ProcessMetrics, current_process_metrics

__all__ = ["ProcessMetrics", "current_process_metrics"]
