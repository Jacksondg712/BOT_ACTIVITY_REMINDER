import json as _json
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings

_BASE_DIR = Path(__file__).parent


def _load_json(name: str) -> dict:
    return _json.loads((_BASE_DIR / name).read_text(encoding="utf-8"))


# Horario, limites de items y quien recibe que - editar reminder_config.json
# sin tocar codigo.
reminder_config: dict = _load_json("reminder_config.json")

# Textos de los mensajes de Slack - editar message_templates.json sin tocar
# codigo (ver README, seccion "Editar los mensajes").
message_templates: dict = _load_json("message_templates.json")


class Settings(BaseSettings):
    # ------------------------------------------------------------------ #
    #  SECRETOS — deben estar en variables de entorno (.env)              #
    # ------------------------------------------------------------------ #
    # Salesforce: usar OAuth (client_id + client_secret) O SOAP (username + password + token)
    salesforce_client_id: str = ""
    salesforce_client_secret: str = ""
    salesforce_username: str = ""
    salesforce_password: str = ""
    salesforce_security_token: str = ""

    salesforce_base_url: str = "https://your-instance.my.salesforce.com"
    salesforce_login_url: str = "https://login.salesforce.com"
    salesforce_api_version: str = "v61.0"

    # --- Slack: misma configuracion de app que BOT_ASSIG_LIDER (Bot Token ---
    # --- Scopes: chat:write, im:write, users:read, users:read.email). ---
    slack_bot_token: str = ""
    slack_notifications_enabled: bool = True
    slack_test_channel_id: str = ""

    # --- Modo prueba: solo se procesan los Ejecutivotest*, y sus mensajes ---
    # --- van al canal de pruebas + DM a Ejecutivotestsend1 (no al dueño ---
    # --- real de las tareas ni a su lider). ---
    test_mode: bool = True
    ejecutivotest1: str = ""
    ejecutivotest2: str = ""
    ejecutivotest3: str = ""
    # En el .env puede llamarse Ejecutivotestsend1 o Ejecutivosendtest1.
    ejecutivotestsend1: str = Field(
        "", validation_alias=AliasChoices("ejecutivotestsend1", "ejecutivosendtest1")
    )

    # Silencia los logs linea-por-linea de httpx/httpcore.
    quiet_http_logs: bool = True

    class Config:
        env_file = _BASE_DIR / ".env"
        extra = "ignore"

    @property
    def test_executive_emails(self) -> list[str]:
        emails = [self.ejecutivotest1, self.ejecutivotest2, self.ejecutivotest3]
        return [email.strip().lower() for email in emails if email and email.strip()]


settings = Settings()
