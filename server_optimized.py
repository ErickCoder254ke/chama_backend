# Performance Optimizations for Render Free Tier
# Add these imports and configurations to server.py

# Add to imports section:
from typing import List, Optional
from functools import wraps
import gzip

# Add after app initialization:
# Response size limit middleware
MAX_RESPONSE_SIZE_MB = 5  # Render free tier friendly
MAX_RESPONSE_SIZE_BYTES = MAX_RESPONSE_SIZE_MB * 1024 * 1024

# Pagination defaults
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

# Helper function for pagination
def paginate_results(items: list, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE):
    """Paginate a list of items"""
    page_size = min(page_size, MAX_PAGE_SIZE)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    
    return {
        "items": items[start_idx:end_idx],
        "page": page,
        "page_size": page_size,
        "total_items": len(items),
        "total_pages": (len(items) + page_size - 1) // page_size,
        "has_more": end_idx < len(items)
    }

# Helper to strip base64 images from responses
def strip_base64_fields(data: dict, fields: list = None) -> dict:
    """Remove or truncate base64 fields to reduce response size"""
    if fields is None:
        fields = ['logo', 'profile_picture', 'receipt_image', 'supporting_documents']
    
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if key in fields and isinstance(value, str) and value.startswith('data:'):
                # Replace with placeholder or truncate
                result[key] = f"[BASE64_DATA_{len(value)}_BYTES]"
            elif isinstance(value, (dict, list)):
                result[key] = strip_base64_fields(value, fields)
            else:
                result[key] = value
        return result
    elif isinstance(data, list):
        return [strip_base64_fields(item, fields) for item in data]
    else:
        return data

# Add timeout decorator for long-running queries
import asyncio
from functools import wraps

def timeout(seconds=10):
    """Timeout decorator for async functions"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await asyncio.wait_for(func(*args, **kwargs), timeout=seconds)
            except asyncio.TimeoutError:
                raise HTTPException(
                    status_code=504,
                    detail="Request timeout. Try reducing the date range or page size."
                )
        return wrapper
    return decorator
