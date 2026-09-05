from utils.bold_fix_bot import _should_convert_markdown


def test_existing_html_with_username_underscore_is_not_reescaped():
    text = "<b>WireGuard</b>\nمثال: <code>arman_panel</code>"
    assert _should_convert_markdown(text) is False


def test_plain_username_underscore_is_not_markdown():
    assert _should_convert_markdown("arman_panel") is False
    assert _should_convert_markdown("ronixnetsupport_3229") is False


def test_real_markdown_is_still_detected():
    assert _should_convert_markdown("**bold**") is True
    assert _should_convert_markdown("`code`") is True
    assert _should_convert_markdown("_italic_") is True


def test_explicit_html_never_converts():
    assert _should_convert_markdown("**literal**", "HTML") is False
