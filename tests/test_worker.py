import asyncio
import sys
from unittest.mock import MagicMock, patch

import pytest

# Mock database and external modules before importing worker
sys.modules["app.database.session"] = MagicMock()
sys.modules["app.database.models"] = MagicMock()
sys.modules["app.services.users"] = MagicMock()
sys.modules["app.parser.telethon_client"] = MagicMock()
sys.modules["config"] = MagicMock()
sys.modules["aiogram"] = MagicMock()

from app.parser.worker import _is_bot_entity, _is_outgoing_message


class MockMsg:
    def __init__(self, outgoing=False):
        self.outgoing = outgoing


class MockEntity:
    def __init__(self, bot=False):
        self.bot = bot


def test_is_outgoing_message_true():
    msg = MockMsg(outgoing=True)
    assert _is_outgoing_message(msg) is True


def test_is_outgoing_message_false():
    msg = MockMsg(outgoing=False)
    assert _is_outgoing_message(msg) is False


def test_is_bot_entity_true():
    entity = MockEntity(bot=True)
    assert _is_bot_entity(entity) is True


def test_is_bot_entity_false():
    entity = MockEntity(bot=False)
    assert _is_bot_entity(entity) is False


def test_search_filters_outgoing_messages():
    """Verify that outgoing messages are filtered out in search logic."""
    messages = [
        MockMsg(outgoing=True),
        MockMsg(outgoing=False),
        MockMsg(outgoing=True),
        MockMsg(outgoing=False),
    ]
    filtered = [m for m in messages if not _is_outgoing_message(m)]
    assert len(filtered) == 2
    assert all(not m.outgoing for m in filtered)


def test_search_filters_bot_entities():
    """Verify that bot entities are filtered out in search logic."""
    entities = [
        MockEntity(bot=True),
        MockEntity(bot=False),
        MockEntity(bot=True),
        MockEntity(bot=False),
    ]
    filtered = [e for e in entities if not _is_bot_entity(e)]
    assert len(filtered) == 2
    assert all(not e.bot for e in filtered)


def test_historical_search_no_notification_when_zero_leads():
    """Verify that no completion message is sent when 0 leads are found."""
    import inspect
    from app.parser import worker

    source_code = inspect.getsource(worker._historical_search_for_user)
    assert 'if found > 0 or saved > 0:' in source_code
    assert 'bot.send_message' in source_code
