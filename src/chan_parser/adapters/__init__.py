"""适配器包。"""

from .csv_adapter import CSVAdapter
from .dataframe_adapter import DataFrameAdapter

__all__ = [
    "CSVAdapter",
    "DataFrameAdapter",
]
