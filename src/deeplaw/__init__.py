"""DeepLaw public API."""

from .models import SearchRequest
from .search import DeepLaw

__all__ = ["DeepLaw", "SearchRequest"]
__version__ = "0.2.0"
