from __future__ import annotations

import os
from pathlib import Path


def _load_env_file() -> None:
    """Load simple KEY=value pairs from backend/.env if present."""
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file()

# ─── Ollama settings ──────────────────────────────────────────────────────────
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:latest")
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.1"))
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "4096"))
OLLAMA_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "700"))

# ─── Search settings ──────────────────────────────────────────────────────────
MAX_SEARCH_RESULTS_PER_QUERY = 5
MAX_QUERIES_PER_CONTACT = 4
MAX_RETRY_COUNT = 2                # retries when search returns < MIN_PRODUCTS
MIN_PRODUCTS_NEEDED = 3
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
TAVILY_SEARCH_DEPTH = os.getenv("TAVILY_SEARCH_DEPTH", "basic")

# ─── E-commerce domains per country ──────────────────────────────────────────
ECOMMERCE_DOMAINS: dict[str, list[str]] = {
    "India":     ["amazon.in", "flipkart.com", "myntra.com", "nykaa.com", "ajio.com"],
    "USA":       ["amazon.com", "target.com", "walmart.com", "bestbuy.com", "etsy.com", "uncommongoods.com", "bookshop.org", "barnesandnoble.com", "rei.com"],
    "UK":        ["amazon.co.uk", "johnlewis.com", "selfridges.com", "notonthehighstreet.com", "marks-and-spencer.com", "waterstones.com"],
    "Singapore": ["amazon.sg", "lazada.sg", "shopee.sg", "zalora.sg", "tangs.com"],
    "Germany":   ["amazon.de", "otto.de", "zalando.de", "mediamarkt.de", "thalia.de"],
    "UAE":       ["amazon.ae", "noon.com", "namshi.com", "carrefouruae.com", "sharafdg.com"],
}

# fallback domains when country is unrecognised
DEFAULT_ECOMMERCE_DOMAINS = ["amazon.com", "etsy.com", "uncommongoods.com"]

# ─── Currency symbols ─────────────────────────────────────────────────────────
CURRENCY_SYMBOLS: dict[str, str] = {
    "INR": "₹",
    "USD": "$",
    "GBP": "£",
    "SGD": "S$",
    "EUR": "€",
    "AED": "AED ",
}

# ─── Safe signal categories (guardrails) ─────────────────────────────────────
SENSITIVE_ATTRIBUTES = [
    "religion", "politics", "health", "ethnicity", "race",
    "gender", "family status", "sexual orientation", "nationality",
    "age", "disability",
]
