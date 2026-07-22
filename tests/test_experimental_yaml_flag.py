# WashData - Home Assistant integration for appliance cycle monitoring via smart plugs.
# Copyright (C) 2026 Lukas Bandura
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The optional global YAML opt-in that unhides the panel's Experimental section:

    ha_washdata:
      experimental: true
"""
import pytest

from custom_components.ha_washdata import async_setup
from custom_components.ha_washdata.const import DOMAIN, EXPERIMENTAL_DATA_KEY


@pytest.mark.asyncio
async def test_async_setup_experimental_flag_on(mock_hass):
    ok = await async_setup(mock_hass, {DOMAIN: {"experimental": True}})
    assert ok is True
    assert mock_hass.data[EXPERIMENTAL_DATA_KEY] is True


@pytest.mark.asyncio
async def test_async_setup_experimental_flag_default_off(mock_hass):
    # No ha_washdata: block in configuration.yaml -> feature stays hidden.
    ok = await async_setup(mock_hass, {})
    assert ok is True
    assert mock_hass.data[EXPERIMENTAL_DATA_KEY] is False


@pytest.mark.asyncio
async def test_async_setup_experimental_flag_explicit_false(mock_hass):
    ok = await async_setup(mock_hass, {DOMAIN: {"experimental": False}})
    assert ok is True
    assert mock_hass.data[EXPERIMENTAL_DATA_KEY] is False
