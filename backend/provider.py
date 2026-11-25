import os

class ProviderNotConfigured(RuntimeError):
    pass

class AuthorizedXProvider:
    """
    Extension point for a documented, authorized X media provider.
    This starter intentionally does not scrape X pages or use session cookies.
    """
    def __init__(self):
        self.provider_name = os.getenv("XENO_X_PROVIDER", "").strip()

    async def extract(self, post_url: str) -> dict:
        raise ProviderNotConfigured(
            "X post extraction is disabled. Connect an approved X API/provider integration "
            "that explicitly permits media retrieval."
        )
