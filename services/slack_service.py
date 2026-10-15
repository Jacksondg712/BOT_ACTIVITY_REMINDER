import logging
from typing import Optional

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from config import settings

logger = logging.getLogger(__name__)


class SlackService:
    """Wrapper minimo sobre AsyncWebClient para enviar recordatorios.

    Usa la misma configuracion de app que BOT_ASSIG_LIDER (ver SLACK_BOT_TOKEN en .env,
    `xoxb-...`), con Bot Token Scopes: chat:write, im:write, users:read,
    users:read.email.
    """

    def __init__(self):
        self.client = AsyncWebClient(token=settings.slack_bot_token)

    async def find_user_id_by_email(self, email: str) -> Optional[str]:
        try:
            resp = await self.client.users_lookupByEmail(email=email)
            if resp["ok"]:
                return resp["user"]["id"]
        except SlackApiError as e:
            error = e.response["error"]
            hint = (
                "ese email no pertenece a ningun usuario de este workspace"
                if error == "users_not_found"
                else "revisar scope users:read.email"
            )
            logger.warning("Slack users_lookupByEmail error para %s: %s (%s)", email, error, hint)
        except Exception:
            logger.exception("Slack users_lookupByEmail error inesperado para %s", email)
        return None

    async def open_dm(self, user_id: str) -> Optional[str]:
        try:
            resp = await self.client.conversations_open(users=user_id)
            if resp["ok"]:
                return resp["channel"]["id"]
        except SlackApiError as e:
            logger.warning(
                "Slack conversations_open error para %s: %s (revisar scope im:write)",
                user_id,
                e.response["error"],
            )
        except Exception:
            logger.exception("Slack conversations_open error inesperado para %s", user_id)
        return None

    async def post_message(
        self,
        channel: str,
        text: str,
        blocks: Optional[list] = None,
    ) -> bool:
        try:
            kwargs: dict = {"channel": channel, "text": text}
            if blocks:
                kwargs["blocks"] = blocks
            await self.client.chat_postMessage(**kwargs)
            return True
        except SlackApiError as e:
            error = e.response["error"]
            hints = {
                "messages_tab_disabled": "activar 'Messages Tab' en api.slack.com/apps → App Home → Show Tabs",
                "not_in_channel": "invitar el bot al canal con /invite",
                "channel_not_found": "revisar el ID del canal o invitar el bot",
            }
            hint = hints.get(error, "revisar scope chat:write")
            logger.warning("Slack chat_postMessage error para canal %s: %s (%s)", channel, error, hint)
        except Exception:
            logger.exception("Slack chat_postMessage error inesperado para canal %s", channel)
        return False

    async def send_dm(self, user_id: str, text: str, blocks: Optional[list] = None) -> bool:
        dm_channel = await self.open_dm(user_id)
        if not dm_channel:
            return False
        return await self.post_message(dm_channel, text, blocks)

    async def auth_test(self) -> Optional[dict]:
        try:
            resp = await self.client.auth_test()
            if resp["ok"]:
                return {"bot": resp.get("user"), "team": resp.get("team"), "bot_user_id": resp.get("user_id")}
        except SlackApiError as e:
            logger.error("Slack auth_test error: %s (revisar SLACK_BOT_TOKEN)", e.response["error"])
        except Exception:
            logger.exception("Slack auth_test error inesperado")
        return None
