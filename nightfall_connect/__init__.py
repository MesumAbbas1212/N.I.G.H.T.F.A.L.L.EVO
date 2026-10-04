"""NIGHTFALL Connect subsystem.

This package adds the local gateway, device registry, pairing flow, and
protocol definitions used by NIGHTFALL AI to reach companion devices.
"""

from .service import NIGHTFALLConnectService, get_service

__all__ = ["NIGHTFALLConnectService", "get_service"]
