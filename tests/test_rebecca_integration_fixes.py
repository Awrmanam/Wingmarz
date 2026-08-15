import pytest

from utils.rebecca import credential_message, parse_service_ids


def test_plan_service_parser_deduplicates_and_preserves_order():
    assert parse_service_ids("1,2,1") == [1, 2]
    assert parse_service_ids(" 2, 1 ") == [2, 1]


@pytest.mark.parametrize("value", ["", "0", "-1", "1,,2", "one", "1.5", "۱"])
def test_plan_service_parser_rejects_malformed_values(value):
    with pytest.raises(ValueError):
        parse_service_ids(value)


def test_customer_credential_message_preserves_exact_username_and_html_safety():
    message = credential_message(
        "armanstore2_support_8090", "p&<secret>", "https://panel.example/login?a=1&b=2", "Lite & Safe"
    )
    assert "armanstore2_support_8090" in message
    assert "<pre>" not in message and "</pre>" not in message
    assert "```" not in message and "**" not in message
    assert "https://panel.example/login?a=1&amp;b=2" in message
    assert "Lite &amp; Safe" in message
    assert "p&amp;&lt;secret&gt;" in message


@pytest.mark.parametrize("name", ["محمد", "محمد رضایی", " Alice Smith "])
def test_unicode_admin_names_are_valid_after_trimming(name):
    normalized = name.strip()
    assert 2 <= len(normalized) <= 100
