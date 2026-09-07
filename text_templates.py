"""Validated Telegram templates and Premium Emoji-aware formatting."""
import re
from string import Formatter
from html.parser import HTMLParser
from urllib.parse import urlsplit

_TOKEN_RE = re.compile(r"\{emoji:[a-z0-9_.-]{1,64}\}")


class PremiumTemplateString(str):
    """String whose .format() keeps {emoji:key} tokens untouched.

    Business templates such as order_approved_user still use Python ``.format``
    for username/password. Without this wrapper ``{emoji:wire}`` would be parsed
    as a Python format field. Tokens are protected, normal fields are formatted,
    then Premium Emoji tokens are restored for the outgoing Bot renderer.
    """

    def format(self, *args, **kwargs):
        tokens: list[str] = []

        def protect(match: re.Match[str]) -> str:
            index = len(tokens)
            tokens.append(match.group(0))
            return f"__WINGMARZ_EMOJI_TOKEN_{index}__"

        protected = _TOKEN_RE.sub(protect, str(self))
        rendered = protected.format(*args, **kwargs)
        for index, token in enumerate(tokens):
            rendered = rendered.replace(f"__WINGMARZ_EMOJI_TOKEN_{index}__", token)
        return PremiumTemplateString(rendered)

    def format_map(self, mapping):
        tokens: list[str] = []

        def protect(match: re.Match[str]) -> str:
            index = len(tokens)
            tokens.append(match.group(0))
            return f"__WINGMARZ_EMOJI_TOKEN_{index}__"

        protected = _TOKEN_RE.sub(protect, str(self))
        rendered = protected.format_map(mapping)
        for index, token in enumerate(tokens):
            rendered = rendered.replace(f"__WINGMARZ_EMOJI_TOKEN_{index}__", token)
        return PremiumTemplateString(rendered)



def fields(body):
    result = set()
    for _literal, name, spec, conversion in Formatter().parse(_TOKEN_RE.sub('', str(body))):
        if name is None:
            continue
        if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*', name) or spec or conversion:
            raise ValueError('فقط متغیرهای معرفی‌شده بدون قالب‌بندی اضافی مجاز هستند.')
        result.add(name)
    return result


class TelegramHTML(HTMLParser):
    allowed = {'b', 'strong', 'i', 'em', 'u', 'ins', 's', 'strike', 'del', 'span', 'tg-spoiler', 'a', 'code', 'pre', 'blockquote', 'tg-emoji'}

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack = []

    def handle_data(self, data):
        if '<' in data or '>' in data or '&' in data:
            raise ValueError('کاراکترهای خاص HTML را به صورت &lt; و &gt; و &amp; بنویسید.')

    def handle_entityref(self, name):
        if name not in {'lt', 'gt', 'amp', 'quot'}:
            raise ValueError('موجودیت HTML پشتیبانی نمی‌شود.')

    def handle_charref(self, name):
        try:
            number = int(name[1:], 16) if name.lower().startswith('x') else int(name)
            if number <= 0 or number > 0x10FFFF:
                raise ValueError
        except ValueError as exc:
            raise ValueError('موجودیت عددی HTML معتبر نیست.') from exc

    def handle_starttag(self, tag, attrs):
        if tag not in self.allowed:
            raise ValueError('تگ HTML پشتیبانی نمی‌شود: ' + tag)
        attrs = dict(attrs)
        allowed_attrs = {'a': {'href'}, 'span': {'class'}, 'code': {'class'}, 'blockquote': {'expandable'}, 'tg-emoji': {'emoji-id'}}.get(tag, set())
        if set(attrs) - allowed_attrs:
            raise ValueError('ویژگی HTML پشتیبانی نمی‌شود.')
        if tag == 'a':
            href = attrs.get('href') or ''
            url = urlsplit(href)
            if url.scheme not in {'http', 'https', 'tg'} or not url.netloc or url.username or url.password:
                raise ValueError('آدرس لینک معتبر نیست.')
        if tag == 'span' and attrs.get('class') != 'tg-spoiler':
            raise ValueError('کلاس span باید tg-spoiler باشد.')
        if tag == 'tg-emoji' and not str(attrs.get('emoji-id', '')).isdigit():
            raise ValueError('شناسه ایموجی معتبر نیست.')
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            raise ValueError('تگ‌های HTML را به ترتیب صحیح ببندید.')

    def handle_startendtag(self, tag, attrs):
        raise ValueError('تگ خودبسته پشتیبانی نمی‌شود.')


def validate_template(body, default):
    required, actual = fields(default), fields(body)
    if required - actual:
        raise ValueError('متغیرهای ضروری حذف شده‌اند: ' + '، '.join('{'+x+'}' for x in sorted(required-actual)))
    if actual - required:
        raise ValueError('متغیر ناشناخته: ' + '، '.join(sorted(actual-required)))
    parser = TelegramHTML()
    parser.feed(body)
    parser.close()
    if parser.stack:
        raise ValueError('تگ HTML بسته نشده است.')
    return body
