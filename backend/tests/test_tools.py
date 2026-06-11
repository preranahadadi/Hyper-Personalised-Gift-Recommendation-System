"""
Tests for tools/search.py — validate_budget, _extract_price, _is_ecommerce.
These functions are pure / stateless so no mocking is needed.
"""
import pytest
from tools.search import (
    build_store_search_queries,
    score_candidate,
    validate_budget,
    _extract_price,
    _is_ecommerce,
    _looks_like_product_url,
)


# ── validate_budget ────────────────────────────────────────────────────────────

class TestValidateBudget:
    def test_inr_within_range(self):
        assert validate_budget("₹3,999", 3000, 5000) is True

    def test_inr_at_minimum(self):
        assert validate_budget("₹3,000", 3000, 5000) is True

    def test_inr_at_maximum(self):
        assert validate_budget("₹5,000", 3000, 5000) is True

    def test_inr_below_minimum(self):
        assert validate_budget("₹500", 3000, 5000) is False

    def test_inr_above_maximum_outside_tolerance(self):
        # 5000 * 1.15 = 5750; 6000 is beyond tolerance
        assert validate_budget("₹6,000", 3000, 5000) is False

    def test_inr_within_15_percent_tolerance(self):
        # 5000 * 1.15 = 5750 → 5500 should pass
        assert validate_budget("₹5,500", 3000, 5000) is True

    def test_none_price_is_optimistic(self):
        assert validate_budget(None, 3000, 5000) is True

    def test_empty_string_is_optimistic(self):
        assert validate_budget("", 3000, 5000) is True

    def test_non_numeric_string_is_optimistic(self):
        assert validate_budget("See product page", 3000, 5000) is True

    def test_usd_within_range(self):
        assert validate_budget("$79.99", 50, 100) is True

    def test_usd_below_minimum(self):
        assert validate_budget("$25.00", 50, 100) is False

    def test_gbp_within_range(self):
        assert validate_budget("£120", 80, 150) is True

    def test_sgd_above_tolerance(self):
        assert validate_budget("S$200", 50, 100) is False

    def test_comma_separated_inr(self):
        # "4,500" should parse to 4500
        assert validate_budget("₹4,500", 3000, 5000) is True


# ── _extract_price ─────────────────────────────────────────────────────────────

class TestExtractPrice:
    def test_inr_symbol(self):
        result = _extract_price("Buy now for ₹3,999 on Amazon")
        assert result == "₹3,999"

    def test_rs_format(self):
        result = _extract_price("Price: Rs. 4500")
        assert result is not None
        assert "4500" in result.replace(",", "")

    def test_inr_text(self):
        result = _extract_price("Total cost: INR 2999 including taxes")
        assert result is not None

    def test_usd(self):
        result = _extract_price("Only $79.99 with free shipping")
        assert result == "$79.99"

    def test_gbp(self):
        result = _extract_price("Available for £95 at John Lewis")
        assert result == "£95"

    def test_sgd(self):
        result = _extract_price("S$89 on Lazada")
        assert result is not None

    def test_eur(self):
        result = _extract_price("Preis: €65,00")
        assert result is not None

    def test_aed(self):
        result = _extract_price("AED 200 delivery included")
        assert result is not None

    def test_no_price_returns_none(self):
        assert _extract_price("Buy this amazing product today") is None

    def test_title_with_price(self):
        result = _extract_price("SG Cricket Bat ₹4,299 Free Delivery India")
        assert result is not None

    def test_price_with_decimal(self):
        result = _extract_price("$49.99 plus tax")
        assert result == "$49.99"


# ── _is_ecommerce ──────────────────────────────────────────────────────────────

class TestIsEcommerce:

    # India
    def test_amazon_india(self):
        assert _is_ecommerce("https://www.amazon.in/dp/B08N5WRWNW", "India") is True

    def test_flipkart(self):
        assert _is_ecommerce("https://www.flipkart.com/product/p123", "India") is True

    def test_myntra(self):
        assert _is_ecommerce("https://www.myntra.com/tshirts/123", "India") is True

    def test_random_blog_india(self):
        assert _is_ecommerce("https://cricketreviews.in/bat-review", "India") is False

    def test_amazon_com_not_india(self):
        # amazon.com is not in the India list
        assert _is_ecommerce("https://www.amazon.com/dp/B08", "India") is False

    # USA
    def test_amazon_us(self):
        assert _is_ecommerce("https://www.amazon.com/dp/B08N5WRWNW", "USA") is True

    def test_bestbuy(self):
        assert _is_ecommerce("https://www.bestbuy.com/product/123", "USA") is True

    def test_amazon_india_wrong_country(self):
        assert _is_ecommerce("https://www.amazon.in/dp/B08", "USA") is False

    # UK
    def test_amazon_uk(self):
        assert _is_ecommerce("https://www.amazon.co.uk/dp/B08", "UK") is True

    def test_john_lewis(self):
        assert _is_ecommerce("https://www.johnlewis.com/product", "UK") is True

    # Singapore
    def test_lazada_sg(self):
        assert _is_ecommerce("https://www.lazada.sg/products/xyz", "Singapore") is True

    def test_shopee_sg(self):
        assert _is_ecommerce("https://shopee.sg/product-123", "Singapore") is True

    # Unknown country falls back to DEFAULT_ECOMMERCE_DOMAINS
    def test_unknown_country_amazon_com(self):
        assert _is_ecommerce("https://www.amazon.com/dp/B08", "Mars") is True

    def test_unknown_country_etsy(self):
        assert _is_ecommerce("https://www.etsy.com/listing/12345", "Mars") is True

    def test_unknown_country_random(self):
        assert _is_ecommerce("https://randomshop.com/product", "Mars") is False


class TestLooksLikeProductUrl:
    def test_amazon_product_url(self):
        assert _looks_like_product_url("https://www.amazon.in/dp/B08N5WRWNW") is True

    def test_amazon_search_url_is_not_product(self):
        assert _looks_like_product_url("https://www.amazon.in/s?k=cricket+gift") is False

    def test_flipkart_product_url(self):
        assert _looks_like_product_url("https://www.flipkart.com/sg-bat/p/itmabc123?pid=BAT123") is True

    def test_flipkart_search_url_is_not_product(self):
        assert _looks_like_product_url("https://www.flipkart.com/search?q=cricket+gift") is False

    def test_nykaa_product_url(self):
        assert _looks_like_product_url("https://www.nykaa.com/some-product/p/123456") is True

    def test_nykaa_search_url_is_not_product(self):
        assert _looks_like_product_url("https://www.nykaa.com/search/result/?q=perfume") is False

    def test_myntra_search_url_is_not_product(self):
        assert _looks_like_product_url("https://www.myntra.com/perfume") is False

    def test_target_product_url(self):
        assert _looks_like_product_url("https://www.target.com/p/notebook/-/A-12345678") is True

    def test_walmart_product_url(self):
        assert _looks_like_product_url("https://www.walmart.com/ip/Some-Product/123456") is True

    def test_etsy_product_url(self):
        assert _looks_like_product_url("https://www.etsy.com/listing/123456/custom-desk-name-plate") is True


class TestGeneralizedSearch:
    def test_store_search_queries_include_country_stores(self):
        queries = build_store_search_queries(["Rust programming book"], "USA", 50, "USD", max_queries=4)
        assert len(queries) == 4
        assert any("site:amazon.com/dp" in q for q in queries)
        assert all("Rust programming book" in q for q in queries)

    def test_candidate_scoring_prefers_exact_product(self):
        exact = {
            "title": "Rust Programming Book",
            "url": "https://www.amazon.com/dp/B08N5WRWNW",
            "source_query": "site:amazon.com/dp Rust programming book USA under 50 USD",
            "estimated_price": "$39.99",
            "is_valid": True,
        }
        fallback = {
            "title": "Marketplace search for Rust programming book",
            "url": "https://www.amazon.com/s?k=rust+programming+book",
            "source_query": "Rust programming book",
            "estimated_price": None,
            "is_valid": False,
        }
        assert score_candidate(exact, "USA", 10, 50) > score_candidate(fallback, "USA", 10, 50)
