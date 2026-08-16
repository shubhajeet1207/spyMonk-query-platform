"""
Authentication and authorization middleware
"""
from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from typing import Optional
from urllib.parse import urlparse
from config import settings

# API Key authentication
api_key_header = APIKeyHeader(name=settings.API_KEY_HEADER, auto_error=False)


def _is_same_origin_request(request: Request) -> bool:
    """
    True when this request is coming from the app's own served frontend on
    the same origin, not a different site or a direct/external caller (curl,
    scripts). A public static JS bundle can't safely hold a real secret, so
    same-origin calls are trusted instead of requiring one.

    Prefers Sec-Fetch-Site: modern browsers send it on every request they
    initiate, including plain GETs, specifically so servers can make this
    kind of decision. Origin is NOT a reliable signal here on its own — it's
    mainly sent for cross-origin/CORS calls and state-changing methods, not
    plain same-origin GETs — so it's kept only as a fallback for older
    browsers that predate Sec-Fetch-Site support.
    """
    sec_fetch_site = request.headers.get("sec-fetch-site")
    if sec_fetch_site is not None:
        return sec_fetch_site == "same-origin"

    origin = request.headers.get("origin")
    if not origin:
        return False
    try:
        origin_host = urlparse(origin).hostname
    except ValueError:
        return False
    return origin_host is not None and origin_host == request.url.hostname


async def verify_api_key(
    request: Request,
    api_key: Optional[str] = Security(api_key_header),
) -> str:
    """
    Verify API key from request header.

    Same-origin requests (the app's own served frontend) are allowed without
    a key. Direct/external callers (curl, scripts, a different site) must
    still supply a valid X-API-Key.

    Args:
        request: The incoming request, used for the same-origin check
        api_key: API key from request header

    Returns:
        The verified API key (or a placeholder for dev-mode/same-origin)

    Raises:
        HTTPException: If API key is missing or invalid
    """
    # In development mode without configured keys, allow access
    if settings.ENVIRONMENT == "development" and not settings.ALLOWED_API_KEYS:
        return "dev-mode"

    if not api_key:
        if _is_same_origin_request(request):
            return "same-origin"
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    if api_key not in settings.ALLOWED_API_KEYS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )

    return api_key


def is_safe_table_name(table_name: str) -> bool:
    """
    Validate table name to prevent SQL injection

    Args:
        table_name: The table name to validate

    Returns:
        True if table name is safe, False otherwise
    """
    # Allow only alphanumeric characters and underscores
    # Must start with a letter
    import re
    pattern = r'^[a-zA-Z][a-zA-Z0-9_]*$'
    return bool(re.match(pattern, table_name)) and len(table_name) <= 64


def sanitize_sql_query(query: str) -> tuple[bool, str]:
    """
    Validate SQL query to prevent dangerous operations.

    Uses sqlparse to strip all comment tokens (both -- and block comments)
    before checking for forbidden keywords, and uses word-boundary matching
    so column names like 'last_update' or 'created_at' are not false-positives.

    Args:
        query: The SQL query to validate

    Returns:
        Tuple of (is_safe, error_message)
    """
    import re
    import sqlparse

    # Strip all SQL comments (-- line comments and /* block comments */)
    stripped = sqlparse.format(query, strip_comments=True).strip()
    stripped_upper = stripped.upper()

    # Only allow SELECT statements
    if not stripped_upper.startswith('SELECT'):
        return False, "Only SELECT queries are allowed"

    # Disallow dangerous statement-level keywords using word boundaries so
    # column names such as 'last_update' or 'created_at' are not blocked.
    dangerous_keywords = [
        'DROP', 'DELETE', 'INSERT', 'UPDATE', 'ALTER', 'CREATE',
        'TRUNCATE', 'REPLACE', 'EXEC', 'EXECUTE', 'ATTACH', 'DETACH', 'PRAGMA',
    ]

    for keyword in dangerous_keywords:
        if re.search(rf'\b{keyword}\b', stripped_upper):
            return False, f"Forbidden keyword '{keyword}' in query"

    # Disallow multiple statements (trailing semicolon is fine, mid-query is not)
    if ';' in stripped.rstrip(';'):
        return False, "Multiple statements not allowed"

    return True, ""


def validate_file_size(file_size: int) -> bool:
    """
    Validate file size is within allowed limits

    Args:
        file_size: Size of file in bytes

    Returns:
        True if file size is acceptable
    """
    max_size_bytes = settings.UPLOAD_MAX_SIZE_MB * 1024 * 1024
    return file_size <= max_size_bytes


def validate_file_extension(filename: str) -> bool:
    """
    Validate file extension is allowed

    Args:
        filename: Name of the uploaded file

    Returns:
        True if file extension is allowed
    """
    import os
    ext = os.path.splitext(filename.lower())[1]
    return ext in settings.ALLOWED_FILE_EXTENSIONS
