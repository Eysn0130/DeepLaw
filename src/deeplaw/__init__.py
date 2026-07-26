"""DeepLaw public API."""

from .context_compiler import compile_context, verify_capsule
from .knowledge_compiler import compile_source
from .knowledge_store import (
    KnowledgeVault,
    default_knowledge_vault,
    initialize_knowledge_vault,
)
from .models import SearchRequest
from .search import DeepLaw

__all__ = [
    "DeepLaw",
    "KnowledgeVault",
    "SearchRequest",
    "compile_context",
    "compile_source",
    "default_knowledge_vault",
    "initialize_knowledge_vault",
    "verify_capsule",
]
__version__ = "0.4.0"
