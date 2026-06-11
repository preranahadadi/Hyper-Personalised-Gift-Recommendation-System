from __future__ import annotations

import json
import re
import time
import urllib.request
from typing import List
from urllib.parse import urlparse

from config import (
    DEFAULT_ECOMMERCE_DOMAINS,
    ECOMMERCE_DOMAINS,
    MAX_SEARCH_RESULTS_PER_QUERY,
    TAVILY_API_KEY,
    TAVILY_SEARCH_DEPTH,
)
from models.schemas import ProductCandidate

STORE_SEARCH_HINTS = {
    "amazon.": "/dp",
    "target.com": "/p",
    "walmart.com": "/ip",
    "bestbuy.com": "/site",
    "etsy.com": "/listing",
    "uncommongoods.com": "/product",
    "bookshop.org": "/p/books",
    "barnesandnoble.com": "/w/",
    "rei.com": "/product",
    "flipkart.com": "/p",
    "myntra.com": "/buy",
    "nykaa.com": "/p",
    "ajio.com": "/p",
    "johnlewis.com": "/",
    "selfridges.com": "/",
    "notonthehighstreet.com": "/",
    "marks-and-spencer.com": "/",
    "waterstones.com": "/book/",
    "lazada.sg": "/products",
    "shopee.sg": "/product",
    "zalora.sg": "/",
    "tangs.com": "/product",
    "otto.de": "/p/",
    "zalando.de": "/",
    "mediamarkt.de": "/",
    "thalia.de": "/shop/home/artikeldetails",
    "amazon.ae": "/dp",
    "noon.com": "/",
    "namshi.com": "/",
    "carrefouruae.com": "/",
    "sharafdg.com": "/",
}


def _get_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


def _is_ecommerce(url: str, country: str) -> bool:
    domain = _get_domain(url)
    trusted = ECOMMERCE_DOMAINS.get(country, DEFAULT_ECOMMERCE_DOMAINS)
    return any(d in domain for d in trusted)


def _is_search_or_category_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower().rstrip("/")
    query = parsed.query.lower()
    search_markers = (
        "/s",
        "/search",
        "/search/result",
        "/shop",
        "/collections",
        "/category",
        "/categories",
        "/browse",
    )
    return (
        any(path == marker or path.startswith(f"{marker}/") for marker in search_markers)
        or "q=" in query
        or "k=" in query
    )


def _looks_like_product_url(url: str) -> bool:
    parsed = urlparse(url)
    domain = parsed.netloc.lower().lstrip("www.")
    path = parsed.path.lower()
    query = parsed.query.lower()

    if _is_search_or_category_url(url):
        return False
    if "amazon." in domain:
        return bool(re.search(r"/(?:dp|gp/product)/[a-z0-9]{8,}", path))
    if "flipkart.com" in domain:
        return "/p/" in path or "pid=" in query
    if "myntra.com" in domain:
        return bool(re.search(r"/\d{5,}(?:/buy)?/?$", path)) or "/buy" in path
    if "nykaa.com" in domain:
        return "/p/" in path or bool(re.search(r"/p/\d+", path))
    if "ajio.com" in domain:
        return bool(re.search(r"/p/\d+", path))
    if "target.com" in domain:
        return "/p/" in path or path.startswith("/p/")
    if "walmart.com" in domain:
        return "/ip/" in path
    if "bestbuy.com" in domain:
        return "/site/" in path and re.search(r"/\d+\.p", path)
    if "etsy.com" in domain:
        return "/listing/" in path
    if "uncommongoods.com" in domain:
        return "/product/" in path
    if "bookshop.org" in domain:
        return "/p/books/" in path or "/a/" in path
    if "barnesandnoble.com" in domain:
        return "/w/" in path
    if "rei.com" in domain:
        return "/product/" in path
    if "johnlewis.com" in domain:
        return bool(re.search(r"/p\d+", path)) or "/p/" in path
    if "selfridges.com" in domain:
        return "/product/" in path or bool(re.search(r"/[a-z0-9-]+-[a-z0-9]+/?$", path))
    if "notonthehighstreet.com" in domain:
        return "/product/" in path
    if "marks-and-spencer.com" in domain:
        return "/p/" in path
    if "waterstones.com" in domain:
        return "/book/" in path
    if "lazada.sg" in domain:
        return "/products/" in path or path.endswith(".html")
    if "shopee.sg" in domain:
        return "-i." in path or "/product/" in path
    if "zalora.sg" in domain:
        return path.endswith(".html")
    if "tangs.com" in domain:
        return "/product/" in path
    if "otto.de" in domain:
        return "/p/" in path
    if "zalando.de" in domain:
        return path.endswith(".html")
    if "mediamarkt.de" in domain:
        return "/product/" in path
    if "thalia.de" in domain:
        return "/shop/home/artikeldetails/" in path
    if "noon.com" in domain:
        return "/p/" in path or path.endswith("/p/")
    if "namshi.com" in domain:
        return "/buy-" in path
    if "carrefouruae.com" in domain:
        return "/p/" in path
    if "sharafdg.com" in domain:
        return "/product/" in path
    return not _is_search_or_category_url(url)


_PRICE_PATTERNS = [
    r"₹\s*[\d,]+(?:\.\d{1,2})?",
    r"â‚¹\s*[\d,]+(?:\.\d{1,2})?",
    r"Rs\.?\s*[\d,]+(?:\.\d{1,2})?",
    r"INR\s*[\d,]+(?:\.\d{1,2})?",
    r"\$\s*[\d,]+(?:\.\d{1,2})?",
    r"USD\s*[\d,]+(?:\.\d{1,2})?",
    r"£\s*[\d,]+(?:\.\d{1,2})?",
    r"Â£\s*[\d,]+(?:\.\d{1,2})?",
    r"GBP\s*[\d,]+(?:\.\d{1,2})?",
    r"S\$\s*[\d,]+(?:\.\d{1,2})?",
    r"SGD\s*[\d,]+(?:\.\d{1,2})?",
    r"€\s*[\d,]+(?:\.\d{1,2})?",
    r"â‚¬\s*[\d,]+(?:\.\d{1,2})?",
    r"EUR\s*[\d,]+(?:\.\d{1,2})?",
    r"AED\s*[\d,]+(?:\.\d{1,2})?",
]


def _extract_price(text: str) -> str | None:
    for pat in _PRICE_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0).strip()
    return None


def _trusted_domains(country: str) -> List[str]:
    return ECOMMERCE_DOMAINS.get(country, DEFAULT_ECOMMERCE_DOMAINS)


def _product_hint_for_domain(domain: str) -> str:
    for marker, hint in STORE_SEARCH_HINTS.items():
        if marker in domain:
            return hint
    return ""


def _clean_intent(intent: str) -> str:
    cleaned = re.sub(r"\bsite:[^\s]+\b", "", intent, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -")
    return cleaned[:120]


def build_store_search_queries(
    product_intents: List[str],
    country: str,
    budget_max: float,
    currency: str,
    max_queries: int = 14,
) -> List[str]:
    """Expand product intents into generalized store-specific public-search queries."""
    domains = _trusted_domains(country)
    intents = [_clean_intent(i) for i in product_intents if _clean_intent(i)]
    if not intents:
        intents = [f"professional gift under {budget_max:g} {currency}"]

    queries: List[str] = []
    domains_per_intent = max(2, min(4, len(domains)))
    for intent_index, intent in enumerate(intents):
        # Rotate store priority so each intent does not always hit the same domains.
        rotated = domains[intent_index % len(domains):] + domains[:intent_index % len(domains)]
        for domain in rotated[:domains_per_intent]:
            hint = _product_hint_for_domain(domain)
            query = f"site:{domain}{hint} {intent} {country} under {budget_max:g} {currency}"
            queries.append(query)
            if len(queries) >= max_queries:
                return list(dict.fromkeys(queries))

    return list(dict.fromkeys(queries))


def _keyword_overlap(title: str, query: str) -> int:
    stop = {
        "the", "and", "for", "with", "under", "buy", "gift", "gifts", "usa",
        "india", "uk", "singapore", "germany", "uae", "usd", "inr", "gbp",
        "sgd", "eur", "aed", "site", "com", "www",
    }
    title_words = set(re.findall(r"[a-z0-9]+", title.lower())) - stop
    query_words = set(re.findall(r"[a-z0-9]+", query.lower())) - stop
    return len(title_words & query_words)


def score_candidate(candidate: dict | ProductCandidate, country: str, budget_min: float, budget_max: float) -> int:
    """Score public-search candidates before LLM ranking."""
    data = candidate.model_dump() if isinstance(candidate, ProductCandidate) else candidate
    url = data.get("url", "")
    title = data.get("title", "")
    query = data.get("source_query", "")
    price = data.get("estimated_price")

    score = 0
    if data.get("is_valid"):
        score += 60
    elif _is_ecommerce(url, country):
        score += 20
    else:
        score -= 30

    if _is_search_or_category_url(url):
        score -= 20
    if price:
        score += 10
        if validate_budget(price, budget_min, budget_max):
            score += 15
        else:
            score -= 20

    score += min(_keyword_overlap(title, query), 6) * 3
    return score


def _candidate_from_result(item: dict, query: str, country: str, provider: str) -> ProductCandidate | None:
    url: str = item.get("href") or item.get("url") or ""
    title: str = item.get("title") or ""
    snippet: str = item.get("body") or item.get("content") or ""

    if not url or not title:
        return None

    domain = _get_domain(url)
    is_ecomm = _is_ecommerce(url, country)
    is_product = is_ecomm and _looks_like_product_url(url)
    price = _extract_price(snippet) or _extract_price(title)

    if is_product:
        reason = f"{provider}: exact product page on trusted e-commerce domain"
    elif is_ecomm:
        reason = f"{provider}: trusted store result, but URL appears to be search/category page"
    else:
        reason = f"{provider}: domain '{domain}' not in trusted list for {country}"

    return ProductCandidate(
        title=title,
        url=url,
        snippet=snippet,
        source_query=query,
        estimated_price=price,
        domain=domain,
        is_valid=is_product,
        validation_reason=reason,
    )


def _search_duckduckgo(queries: List[str], country: str) -> List[ProductCandidate]:
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            raise ImportError("Search package not installed. Run: pip install ddgs")

    candidates: List[ProductCandidate] = []
    with DDGS() as ddgs:
        for query in queries:
            try:
                time.sleep(0.5)
                raw = list(ddgs.text(query, max_results=MAX_SEARCH_RESULTS_PER_QUERY))
            except Exception as exc:
                print(f"[search:duckduckgo] query failed ({exc}): {query}")
                continue

            for item in raw:
                candidate = _candidate_from_result(item, query, country, "DuckDuckGo")
                if candidate:
                    candidates.append(candidate)
    return candidates


def _search_tavily(queries: List[str], country: str) -> List[ProductCandidate]:
    if not TAVILY_API_KEY:
        return []

    candidates: List[ProductCandidate] = []
    trusted = ECOMMERCE_DOMAINS.get(country, DEFAULT_ECOMMERCE_DOMAINS)
    tavily_country = country.lower() if country else None

    for query in queries:
        payload = {
            "query": query,
            "search_depth": TAVILY_SEARCH_DEPTH,
            "max_results": MAX_SEARCH_RESULTS_PER_QUERY,
            "include_domains": trusted,
            "include_answer": False,
            "include_raw_content": False,
        }
        if tavily_country:
            payload["country"] = tavily_country

        req = urllib.request.Request(
            "https://api.tavily.com/search",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {TAVILY_API_KEY}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=12) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            print(f"[search:tavily] query failed ({exc}): {query}")
            continue

        for item in raw.get("results", []):
            candidate = _candidate_from_result(item, query, country, "Tavily")
            if candidate:
                candidates.append(candidate)

    return candidates


def search_products(queries: List[str], country: str) -> List[ProductCandidate]:
    """Run Tavily, when configured, plus DuckDuckGo and deduplicate product candidates."""
    candidates: List[ProductCandidate] = []
    seen_urls: set[str] = set()

    for candidate in _search_tavily(queries, country) + _search_duckduckgo(queries, country):
        if candidate.url in seen_urls:
            continue
        seen_urls.add(candidate.url)
        candidates.append(candidate)

    return candidates


def validate_budget(price_str: str | None, budget_min: float, budget_max: float) -> bool:
    """Return True if the extracted price string falls within the budget range."""
    if not price_str:
        return True
    digits = re.sub(r"[^\d.]", "", price_str.replace(",", ""))
    try:
        value = float(digits)
        return budget_min <= value <= budget_max * 1.15
    except ValueError:
        return True
