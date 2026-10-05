"""Cascade CMS REST API client library.

Provides an async REST driver, a fluent Operations builder that composes
requests and callbacks into linked operation chains, Pydantic-based
payload/response models, and an OperationLogger for console/logfile output.
"""

from .driver import CascadeCMSRestDriver, RequestExecutor
from .operations import Node, OperationChain, Operations
from .utils.operation_logger import OperationLogger
from .wrapper import Cascade

CascadeWrapperBase = Cascade

__all__ = [
    "Cascade",
    "CascadeCMSRestDriver",
    "CascadeWrapperBase",
    "Node",
    "OperationChain",
    "OperationLogger",
    "Operations",
    "RequestExecutor",
]
