from urllib.parse import urlparse

X_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}

def is_x_post_url(url: str) -> bool:
    try:
        p = urlparse(url)
        if p.scheme != "https" or (p.hostname or "").lower() not in X_HOSTS:
            return False
        parts = [part for part in p.path.split("/") if part]
        return len(parts) >= 3 and parts[1] == "status" and parts[2].isdigit()
    except (TypeError, ValueError):
        return False

def enforce_content_policy(visibility: str, *, authorized_access: bool = False,
                           age_eligible: bool = False) -> None:
    state = (visibility or "unknown").strip().lower()
    if state in {"private", "protected"}:
        raise PermissionError("Private/protected posts are not supported.")
    if state in {"sensitive", "age-restricted", "age_restricted"}:
        if not (authorized_access and age_eligible):
            raise PermissionError(
                "Sensitive or age-restricted content requires verified authorized access and age eligibility."
            )
