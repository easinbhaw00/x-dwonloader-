import pytest
from backend.policy import is_x_post_url, enforce_content_policy

@pytest.mark.parametrize("url", [
    "https://x.com/example/status/123456",
    "https://twitter.com/example/status/123456",
    "https://www.x.com/example/status/123456"
])
def test_valid_x_post_urls(url):
    assert is_x_post_url(url)

@pytest.mark.parametrize("url", [
    "http://x.com/example/status/123",
    "https://x.com.evil.test/example/status/123",
    "https://x.com/example/status/not-a-number",
    "https://youtube.com/watch?v=123",
    "javascript:alert(1)"
])
def test_invalid_x_post_urls(url):
    assert not is_x_post_url(url)

def test_private_posts_always_blocked():
    with pytest.raises(PermissionError):
        enforce_content_policy("private", authorized_access=True, age_eligible=True)

def test_sensitive_requires_authorized_access_and_age_eligibility():
    with pytest.raises(PermissionError):
        enforce_content_policy("sensitive", authorized_access=True, age_eligible=False)
    with pytest.raises(PermissionError):
        enforce_content_policy("age-restricted", authorized_access=False, age_eligible=True)
    enforce_content_policy("sensitive", authorized_access=True, age_eligible=True)

def test_public_post_allowed():
    enforce_content_policy("public")


def test_protected_posts_blocked_even_with_access_claim():
    with pytest.raises(PermissionError):
        enforce_content_policy("protected", authorized_access=True, age_eligible=True)
