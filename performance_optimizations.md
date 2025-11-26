# Performance Optimizations for Render Free Tier

## Critical Changes Needed

### 1. Limit Database Query Results
Replace all `.to_list(10000)` with `.to_list(100)` or add pagination

### 2. Add Compression Middleware
```python
from starlette.middleware.gzip import GZIPMiddleware
app.add_middleware(GZIPMiddleware, minimum_size=1000)
```

### 3. Field Projection
Only return needed fields from MongoDB:

```python
# Instead of:
user = await db.users.find_one({"_id": ObjectId(user_id)})

# Use projection:
user = await db.users.find_one(
    {"_id": ObjectId(user_id)},
    {"name": 1, "phone": 1, "email": 1}  # Only get needed fields
)
```

### 4. Remove Base64 Images from List Endpoints
Only return image URLs/IDs, not full base64 data

### 5. Add Pagination to All List Endpoints
```python
@api_router.get("/contributions/{chama_id}")
async def get_contributions(
    chama_id: str,
    page: int = 1,
    page_size: int = 20,
    current_user: dict = Depends(get_current_user)
):
    # Use skip and limit
    skip = (page - 1) * page_size
    contributions = await db.contributions.find(
        {"chama_id": chama_id}
    ).skip(skip).limit(page_size).to_list(page_size)
```

### 6. Add Indexes
```python
# Create indexes for frequently queried fields
await db.contributions.create_index([("chama_id", 1), ("status", 1)])
await db.members.create_index([("chama_id", 1), ("user_id", 1)])
await db.loans.create_index([("chama_id", 1), ("status", 1)])
```

### 7. Use Aggregation Pipeline Instead of Multiple Queries
Reduce number of database calls by using MongoDB aggregation

### 8. Cache Frequently Accessed Data
Use in-memory caching for dashboard stats

## Implementation Priority

1. **CRITICAL**: Limit all `.to_list()` to max 100-500 items
2. **CRITICAL**: Add GZIP compression
3. **HIGH**: Add pagination to list endpoints
4. **HIGH**: Remove base64 from list responses
5. **MEDIUM**: Add field projection
6. **MEDIUM**: Add database indexes
7. **LOW**: Implement caching
