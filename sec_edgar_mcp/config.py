from .utils.sec_http import declared_user_agent


def initialize_config() -> str:
    """The declared SEC User-Agent (SEC_EDGAR_USER_AGENT); raises if it names no real contact."""
    return declared_user_agent()
