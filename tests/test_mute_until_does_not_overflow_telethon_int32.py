from unittest.mock import AsyncMock, MagicMock

import pytest

from admin_console.agents.memberships import _set_mute_status


@pytest.mark.asyncio
async def test_set_mute_status_caps_mute_until_to_int32_max():
    """
    Regression test: Telethon can encode mute_until as a 32-bit signed int.
    Ensure we never pass a value > 2^31-1.
    """
    client = AsyncMock()
    client.get_input_entity = AsyncMock(return_value=MagicMock())

    async def _fake_call(request):
        settings = request.settings
        assert settings.mute_until <= 2_147_483_647
        return None

    client.side_effect = _fake_call

    # entity content doesn't matter for this test
    await _set_mute_status(client, entity=MagicMock(), mute=True)

