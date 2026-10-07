import os


def initialize_config():
    """Initialize the SEC EDGAR configuration"""
    sec_edgar_user_agent = os.getenv("SEC_EDGAR_USER_AGENT")
    if not sec_edgar_user_agent:
        # Use a default user agent for MCP server
        sec_edgar_user_agent = "claude-code-mcp-client developer@example.com"

    return sec_edgar_user_agent
