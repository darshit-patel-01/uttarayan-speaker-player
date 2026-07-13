from fastapi import Request


def detect_source(request: Request) -> str:
    """
    Returns 'whatsapp' or 'telegram' if the request came from the matching
    bridge (X-Source header, or WhatsApp's in-app browser UA as a fallback),
    'web' for regular browsers, or 'api' for other programmatic calls.
    """
    source_header = request.headers.get("x-source")
    if source_header in ("whatsapp", "telegram"):
        return source_header
    ua = request.headers.get("user-agent", "")
    if "WhatsApp" in ua:
        return "whatsapp"
    if ua:
        return "web"
    return "api"


def detect_requester_id(request: Request, source: str) -> str:
    """
    Identity used for rate limiting. The WhatsApp/Telegram bridges send the
    sender's phone number / Telegram user id via X-Requester-Id; plain web
    or API callers have no such identity, so the client IP is used instead.
    Prefixed by source so e.g. a Telegram numeric id can never collide with
    an IP-derived web identity.
    """
    explicit_id = request.headers.get("x-requester-id")
    if explicit_id:
        return f"{source}:{explicit_id}"
    client_host = request.client.host if request.client else "unknown"
    return f"ip:{client_host}"
