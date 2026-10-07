import json
from typing import Dict, Optional
from .exceptions import APIError
from .sec_http import sec_get_text


class TickerCache:
    """Cache for ticker to CIK mapping."""

    def __init__(self, user_agent: str = None):
        self._cache: Optional[Dict[str, int]] = None

    def get_cik(self, ticker: str) -> Optional[int]:
        """Get CIK for a ticker symbol."""
        if self._cache is None:
            self._load_cache()

        ticker_upper = ticker.upper()
        return self._cache.get(ticker_upper)

    def _load_cache(self) -> None:
        """Load ticker to CIK mapping from SEC."""
        try:
            url = "https://www.sec.gov/files/company_tickers_exchange.json"
            data = json.loads(sec_get_text(url))
            self._cache = {}

            # Handle both dict and list formats
            data_items = data.get("data", data) if isinstance(data, dict) else data

            if isinstance(data_items, dict):
                for company_data in data_items.values():
                    if isinstance(company_data, list) and len(company_data) >= 3:
                        cik = company_data[0]
                        ticker = company_data[2]
                        if ticker:
                            self._cache[ticker.upper()] = cik
            elif isinstance(data_items, list):
                for company_data in data_items:
                    if isinstance(company_data, list) and len(company_data) >= 3:
                        cik = company_data[0]
                        ticker = company_data[2]
                        if ticker:
                            self._cache[ticker.upper()] = cik

        except Exception as e:
            raise APIError(f"Failed to fetch ticker to CIK mapping: {str(e)}")

    def clear(self) -> None:
        """Clear the cache."""
        self._cache = None
