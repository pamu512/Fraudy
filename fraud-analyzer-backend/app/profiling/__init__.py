"""Dataset profiling: schema inference, identifier detection, stats, correlations."""

from app.profiling.profiler import profile_dataframe, profile_upload
from app.profiling.types import DatasetProfile

__all__ = ["DatasetProfile", "profile_dataframe", "profile_upload"]
