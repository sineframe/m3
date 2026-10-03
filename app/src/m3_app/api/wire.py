"""Compatibility exports for the SDK-owned neutral v2 wire mappings."""

from m3._wire import internalize_request, neutralize_openapi, neutralize_response

__all__ = ["internalize_request", "neutralize_openapi", "neutralize_response"]
