"""Regression tests for Telegram's "text must be non-empty" rejection.

Telegram rejects a message whose MarkdownV2 body parses to zero visible
characters.  The common trigger is a fenced code block wrapping empty command
output: ``"```\\n```"`` is non-empty to Python but renders to nothing on
Telegram, so ``send()`` used to raise ``BadRequest`` on every attempt.  The
error was also classified as retryable, so the adapter re-sent the identical
payload twice before giving up and replacing the reply with a delivery-failure
notice — a silently swallowed turn, indistinguishable from the agent ignoring
the user.

These tests pin the two halves of the fix: content that renders empty is
replaced with a visible placeholder, and the rejection is non-retryable.
"""

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig


# ---------------------------------------------------------------------------
# Mock the telegram package if it's not installed
# ---------------------------------------------------------------------------

def _ensure_telegram_mock():
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return
    mod = MagicMock()
    mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
    mod.constants.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    mod.constants.ChatType.GROUP = "group"
    mod.constants.ChatType.SUPERGROUP = "supergroup"
    mod.constants.ChatType.CHANNEL = "channel"
    mod.constants.ChatType.PRIVATE = "private"
    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, mod)


_ensure_telegram_mock()

from gateway.platforms.telegram import (  # noqa: E402
    TelegramAdapter,
    _escape_mdv2,
    _renders_empty_mdv2,
)

BT = "`" * 3
PLACEHOLDER = _escape_mdv2(TelegramAdapter.EMPTY_RENDER_PLACEHOLDER)


@pytest.fixture()
def adapter():
    config = PlatformConfig(enabled=True, token="fake-token")
    return TelegramAdapter(config)


@pytest.fixture()
def sent(adapter):
    """Capture every text handed to bot.send_message."""
    texts = []

    async def _fake_send_message(**kwargs):
        texts.append(kwargs["text"])
        msg = MagicMock()
        msg.message_id = len(texts)
        return msg

    adapter._bot = MagicMock()
    adapter._bot.send_message = AsyncMock(side_effect=_fake_send_message)
    return texts


# =========================================================================
# _renders_empty_mdv2
# =========================================================================

@pytest.mark.parametrize("text", [
    "",
    BT + "\n" + BT,             # empty fence — the real-world trigger
    BT + "text\n" + BT,         # empty fence with a language tag
    BT + "\n\n" + BT,           # fence around a blank line
    BT + "\n   \n" + BT,        # fence around whitespace
    "​",                   # zero-width space
    "​​​",
    "﻿",                   # BOM / zero-width no-break space
    "\x00",                     # NUL
    "\x1b",                     # bare ESC
    "   \n\t ",
])
def test_renders_empty_detects_invisible_payloads(text):
    assert _renders_empty_mdv2(text) is True


@pytest.mark.parametrize("text", [
    "hello",
    "0",
    BT + "\nls -la\n" + BT,
    BT + "\n0\n" + BT,
    _escape_mdv2("**"),         # escaped markers are visible literals
    _escape_mdv2("``"),
    _escape_mdv2("."),
    "*bold*",
    "​x",                  # invisible char plus real content
])
def test_renders_empty_keeps_visible_payloads(text):
    assert _renders_empty_mdv2(text) is False


# =========================================================================
# send() — placeholder substitution
# =========================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize("content", [
    BT + "\n" + BT,
    BT + "log\n" + BT,
    BT + "\n\n" + BT,
    "​",
    "\x00",
])
async def test_send_substitutes_placeholder_for_empty_render(adapter, sent, content):
    """A body that renders to nothing is replaced, never sent blank."""
    result = await adapter.send("123", content)

    assert result.success is True
    assert sent == [PLACEHOLDER]


@pytest.mark.asyncio
async def test_send_never_hands_blank_text_to_telegram(adapter, sent):
    for content in (BT + "\n" + BT, "​", "\x00", BT + "\n   \n" + BT):
        await adapter.send("123", content)
    assert sent, "expected sends to occur"
    assert all(t and t.strip() for t in sent)


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [
    "hello world",
    "0",
    BT + "\nls -la\n" + BT,
    "**bold** text",
])
async def test_send_leaves_visible_content_untouched(adapter, sent, content):
    result = await adapter.send("123", content)

    assert result.success is True
    assert len(sent) == 1
    assert sent[0] != PLACEHOLDER


@pytest.mark.asyncio
async def test_send_still_skips_whitespace_only_content(adapter, sent):
    """Whitespace-only input is dropped before formatting, as before."""
    result = await adapter.send("123", "   \n  ")

    assert result.success is True
    assert sent == []


@pytest.mark.asyncio
async def test_multi_chunk_indicators_keep_every_chunk(adapter, sent):
    """Chunk numbering stays consistent — indicators are always visible."""
    adapter.MAX_MESSAGE_LENGTH = 80
    content = ("chunk content here " * 12).strip()

    result = await adapter.send("123", content)

    assert result.success is True
    assert len(sent) > 1
    assert all(t and t.strip() for t in sent)
    assert PLACEHOLDER not in sent


# =========================================================================
# send() — the rejection must not be retried
# =========================================================================

@pytest.mark.asyncio
async def test_finalize_edit_substitutes_placeholder(adapter):
    """The terminal streaming edit is what the user is left looking at."""
    adapter._bot = MagicMock()
    adapter._bot.edit_message_text = AsyncMock()

    result = await adapter.edit_message("123", "9", BT + "\n" + BT, finalize=True)

    assert result.success is True
    text = adapter._bot.edit_message_text.await_args.kwargs["text"]
    assert text == PLACEHOLDER


@pytest.mark.asyncio
async def test_finalize_edit_leaves_visible_content_alone(adapter):
    adapter._bot = MagicMock()
    adapter._bot.edit_message_text = AsyncMock()

    await adapter.edit_message("123", "9", "real answer", finalize=True)

    text = adapter._bot.edit_message_text.await_args.kwargs["text"]
    assert text == "real answer"


@pytest.mark.asyncio
async def test_empty_body_rejection_is_not_retryable(adapter):
    """Retrying an identical empty body can only waste the user's time."""
    adapter._bot = MagicMock()
    adapter._bot.send_message = AsyncMock(
        side_effect=Exception("Text must be non-empty")
    )

    result = await adapter.send("123", "some content")

    assert result.success is False
    assert result.retryable is False
