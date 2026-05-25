"""Shared MarkdownV2 escaping utilities for daily message sections."""

_MARKDOWN_V2_SPECIAL = r"_*[]()~`>#+-=|{}.!\\"
_MARKDOWN_V2_TABLE = str.maketrans({ch: f"\\{ch}" for ch in _MARKDOWN_V2_SPECIAL})


def md(text: str) -> str:
    """Escape ``text`` for Telegram MarkdownV2."""
    return text.translate(_MARKDOWN_V2_TABLE)


def fmt_temp(temp: float) -> str:
    """Format a temperature value with explicit sign prefix."""
    rounded = round(temp)
    return f"+{rounded}" if rounded >= 0 else str(rounded)
