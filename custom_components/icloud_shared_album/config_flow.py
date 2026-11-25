"""Config flow: paste the album's public link."""
from __future__ import annotations

import secrets
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AlbumError, AlbumNotPublic, SharedAlbumClient, parse_link
from .const import CONF_KEY, CONF_SCAN_MINUTES, CONF_URL, DEFAULT_SCAN_MINUTES, DOMAIN


class SharedAlbumConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].strip()
            parsed = parse_link(url)
            if not parsed:
                errors[CONF_URL] = "invalid_link"
            else:
                await self.async_set_unique_id(parsed[0])
                self._abort_if_unique_id_configured()
                try:
                    title, photos = await SharedAlbumClient(async_get_clientsession(self.hass), url).async_fetch()
                except AlbumNotPublic:
                    errors["base"] = "not_public"
                except AlbumError:
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_create_entry(
                        title=title or "Shared Album",
                        data={CONF_URL: url, CONF_KEY: secrets.token_urlsafe(24)},
                    )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_URL): str}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SharedAlbumOptionsFlow()


class SharedAlbumOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = self.config_entry.options.get(CONF_SCAN_MINUTES, DEFAULT_SCAN_MINUTES)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {vol.Required(CONF_SCAN_MINUTES, default=current): vol.All(vol.Coerce(int), vol.Range(min=5, max=1440))}
            ),
        )
