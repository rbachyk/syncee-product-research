"""Unit tests for value normalization (spec §18, §41.1)."""

from syncee_scanner.extraction import normalization as n


class TestText:
    def test_collapses_and_trims_whitespace(self):
        assert n.normalize_text("  hello   world \n") == "hello world"

    def test_empty_returns_none(self):
        assert n.normalize_text("   ") is None
        assert n.normalize_text(None) is None

    def test_slugify(self):
        assert n.slugify("Rösle Küchen  Tool!") == "rosle-kuchen-tool"
        assert n.slugify(None) == ""


class TestUrl:
    def test_strips_tracking_and_trailing_slash(self):
        url = "https://Example.com/Product/123/?utm_source=x&gclid=y&color=red"
        assert n.normalize_url(url) == "https://example.com/Product/123?color=red"

    def test_sorted_query_is_stable(self):
        a = n.normalize_url("https://e.com/p?b=2&a=1")
        b = n.normalize_url("https://e.com/p?a=1&b=2")
        assert a == b == "https://e.com/p?a=1&b=2"

    def test_drops_fragment(self):
        assert n.normalize_url("https://e.com/p#section") == "https://e.com/p"

    def test_none(self):
        assert n.normalize_url(None) is None
        assert n.normalize_url("") is None


class TestCountry:
    def test_aliases(self):
        assert n.normalize_country("españa") == "Spain"
        assert n.normalize_country("Deutschland") == "Germany"
        assert n.normalize_country("USA") == "United States"

    def test_list_dedupes_and_normalizes(self):
        assert n.normalize_country_list("Spain, españa; Germany") == ["Spain", "Germany"]

    def test_list_from_iterable(self):
        assert n.normalize_country_list(["Italia", "italia"]) == ["Italy"]


class TestPrice:
    def test_plain(self):
        assert n.normalize_price("12.50") == 12.5
        assert n.normalize_price(19) == 19.0

    def test_european_decimal_comma(self):
        assert n.normalize_price("12,50 €") == 12.5

    def test_thousands_separators(self):
        assert n.normalize_price("1,250") == 1250.0
        assert n.normalize_price("1.250,75") == 1250.75
        assert n.normalize_price("1,250.75") == 1250.75

    def test_invalid(self):
        assert n.normalize_price("N/A") is None
        assert n.normalize_price(None) is None


class TestDate:
    def test_iso_z(self):
        assert n.normalize_datetime("2026-01-02T03:04:05Z") == "2026-01-02T03:04:05+00:00"

    def test_epoch(self):
        assert n.normalize_datetime(0) == "1970-01-01T00:00:00+00:00"

    def test_invalid(self):
        assert n.normalize_datetime("not a date") is None
        assert n.normalize_datetime(None) is None


class TestBool:
    def test_true_false_unknown(self):
        assert n.normalize_bool("Yes") is True
        assert n.normalize_bool("out of stock") is False
        assert n.normalize_bool("maybe") is None
        assert n.normalize_bool(True) is True
