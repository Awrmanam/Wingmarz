from __future__ import annotations

import ast
from pathlib import Path

import config
from handlers.operations import MENU_SPECS
from ui_presentation_registry import BUTTONS, SCREENS, resolve_button


ROOT = Path(__file__).resolve().parents[1]
HANDLERS = ROOT / "handlers"
CONSTRUCTORS = {"InlineKeyboardButton", "styled_button", "_button", "_btn"}


def _literal(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Subscript):
        value = node.value
        if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
            if value.value.id == "config" and value.attr == "BUTTONS":
                key = _literal(node.slice)
                return config.BUTTONS.get(key) if key else None
    return None


def _static_buttons():
    rows = []
    for path in sorted(HANDLERS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name not in CONSTRUCTORS:
                continue
            kwargs = {kw.arg: _literal(kw.value) for kw in node.keywords if kw.arg}
            text = _literal(node.args[0]) if node.args else kwargs.get("text")
            callback = _literal(node.args[1]) if len(node.args) > 1 else kwargs.get("callback_data")
            if callback and text:
                rows.append((path.name, node.lineno, callback, text))
    return rows


def test_every_registered_button_points_to_a_real_screen():
    missing = sorted({button.screen for button in BUTTONS if button.screen not in SCREENS})
    assert not missing, f"registered buttons reference unknown screens: {missing}"


def test_dashboard_menu_specs_are_all_explicitly_registered():
    missing = []
    for callback, text, _icon, _fallback in MENU_SPECS:
        resolution = resolve_button(callback, text)
        if resolution.button is None:
            missing.append((callback, text, resolution.excluded_reason))
    assert not missing, f"dashboard buttons missing from registry: {missing}"


def test_all_static_handler_buttons_are_registered_or_intentionally_excluded():
    missing = []
    for filename, lineno, callback, text in _static_buttons():
        result = resolve_button(callback, text)
        if result.button is None and result.excluded_reason is None:
            missing.append(f"{filename}:{lineno}  {callback!r}  {text!r}")
    assert not missing, "Static visible buttons need presentation metadata or an explicit exclusion:\n" + "\n".join(missing[:120])


def test_normal_registry_never_uses_developer_language():
    forbidden = ("callback", "handler", "legacy", "compat", "fsm", "route", "provider", "service id")
    bad = []
    for screen in SCREENS.values():
        lower = screen.title.lower()
        if any(token in lower for token in forbidden):
            bad.append(screen.title)
    for button in BUTTONS:
        lower = button.title.lower()
        if any(token in lower for token in forbidden):
            bad.append(button.title)
    assert not bad, f"developer-facing labels leaked into normal registry: {bad}"


def test_dynamic_records_are_excluded_from_normal_editor():
    samples = [
        ("planmarket:c:p:5", "WireGuard"),
        ("planmarket:d:p:5:30", "یک ماهه"),
        ("planmarket:p:p:5:14", "اقتصادی"),
        ("trialv2:cfg:7", "WireGuard"),
        ("cc:user:123456", "123456"),
        ("cc:order:91", "سفارش 91"),
        ("ops:disc:item:4", "RETURN10"),
        ("order_approve_91", "تأیید و صدور"),
    ]
    for callback, text in samples:
        result = resolve_button(callback, text)
        assert result.button is None and result.excluded_reason
