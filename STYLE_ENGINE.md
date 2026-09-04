# Premium Emoji / Style Engine

Phase 2 adds presentation-only styling. Business identifiers, provider names, plan/service IDs, and callback data remain unchanged.

## Admin access

SUDO admins can open the style manager with `/style` or from the existing Settings menu.

The manager supports:

- global style enable/disable;
- Premium Custom Emoji registration from a Telegram `custom_emoji` entity;
- per-slot Unicode fallbacks;
- replacement, enable/disable, fallback editing and removal;
- preview;
- exact-match text overrides keyed by `(scope, raw_identity)`.

## Storage

The engine creates migration-safe standalone SQLite tables on startup:

- `styled_emojis`
- `styled_settings`
- `styled_text_overrides`

They have no foreign keys into plans, orders, panels, admins, or Rebecca services.

## Rendering rules

When styling is enabled and a configured item is active, message-level premium emoji uses Telegram HTML:

```html
<tg-emoji emoji-id="CUSTOM_EMOJI_ID">fallback</tg-emoji>
```

Otherwise the Unicode fallback is used.

Inline-button custom emoji support is feature-detected from the installed Aiogram model. When unavailable, the same helper keeps all button actions/callbacks unchanged and prefixes the Unicode fallback to the visible label.

Text overrides are exact-match presentation aliases only. The engine always returns the original raw identity separately from its visible label and never fuzzy-matches identities.
