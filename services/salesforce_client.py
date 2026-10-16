import logging
from dataclasses import dataclass
from datetime import date
from html import escape
from typing import Any, Iterable, Optional
from xml.etree import ElementTree as ET

import httpx

from config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0)

_USER_FIELDS = (
    "Id, Name, FirstName, Email, IsActive, Title, "
    "ManagerId, Manager.Name, Manager.FirstName, Manager.Email, Manager.IsActive"
)
_TASK_FIELDS = (
    "Id, Subject, ActivityDate, Status, Priority, Type, OwnerId, "
    "Who.Name, Who.Type, What.Name, What.Type"
)


class SalesforceAuthError(RuntimeError):
    pass


class SalesforceApiError(RuntimeError):
    pass


@dataclass
class SalesforceSession:
    access_token: str
    instance_url: str


def _soql_quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _csv_quoted(values: Iterable[str]) -> str:
    return ", ".join(_soql_quote(value) for value in values)


def _soql_date(day: date) -> str:
    # Los literales de fecha en SOQL van sin comillas: 2026-10-14
    return day.isoformat()


class SalesforceClient:
    """Cliente minimo de solo lectura para el recordatorio de actividades.

    Mismo patron de login (OAuth password flow o SOAP) que
    BOT_ASSIG_LIDER/services/salesforce_client.py.
    """

    def __init__(self):
        self._base = settings.salesforce_base_url.rstrip("/")
        self._login_url = settings.salesforce_login_url.rstrip("/") or self._base
        self._api_version = settings.salesforce_api_version
        self._session: Optional[SalesforceSession] = None

    # ------------------------------------------------------------------ #
    #  Usuarios                                                           #
    # ------------------------------------------------------------------ #
    async def find_active_users_by_emails(self, emails: Iterable[str]) -> list[dict[str, Any]]:
        emails_list = [email for email in emails if email]
        logger.info("SF find_active_users_by_emails: %s", emails_list)
        if not emails_list:
            return []
        return await self.query_all(
            f"SELECT {_USER_FIELDS} FROM User "
            f"WHERE IsActive = true AND Email IN ({_csv_quoted(emails_list)}) "
            "ORDER BY Name ASC"
        )

    async def find_active_users_by_titles(self, titles: Iterable[str]) -> list[dict[str, Any]]:
        titles_list = [title for title in titles if title]
        logger.info("SF find_active_users_by_titles: %s", titles_list)
        if not titles_list:
            return []
        return await self.query_all(
            f"SELECT {_USER_FIELDS} FROM User "
            f"WHERE IsActive = true AND Title IN ({_csv_quoted(titles_list)}) "
            "ORDER BY Name ASC"
        )

    # ------------------------------------------------------------------ #
    #  Tareas                                                             #
    # ------------------------------------------------------------------ #
    async def count_open_tasks_by_owner(
        self,
        owner_ids: Iterable[str],
        today: date,
    ) -> dict[str, dict[str, int]]:
        """{OwnerId: {"overdue": n, "today": n}} de Tasks abiertas.

        Vencida = ActivityDate < hoy; del dia = ActivityDate = hoy. Se usa la
        fecha calculada en Python (zona del bot) en vez de TODAY de SOQL,
        que depende de la zona horaria del usuario de integracion."""
        ids = [owner_id for owner_id in owner_ids if owner_id]
        counts: dict[str, dict[str, int]] = {owner_id: {"overdue": 0, "today": 0} for owner_id in ids}
        if not ids:
            return counts

        for key, condition in (
            ("overdue", f"ActivityDate < {_soql_date(today)}"),
            ("today", f"ActivityDate = {_soql_date(today)}"),
        ):
            for chunk in _chunks(ids, 200):
                records = await self.query_all(
                    "SELECT OwnerId, COUNT(Id) total FROM Task "
                    f"WHERE OwnerId IN ({_csv_quoted(chunk)}) AND IsClosed = false "
                    f"AND {condition} GROUP BY OwnerId"
                )
                for record in records:
                    counts.setdefault(record["OwnerId"], {"overdue": 0, "today": 0})
                    counts[record["OwnerId"]][key] = int(record.get("total") or 0)
        return counts

    async def find_overdue_tasks(
        self,
        owner_id: str,
        today: date,
        limit: int,
        newest_first: bool = True,
    ) -> list[dict[str, Any]]:
        order = "DESC" if newest_first else "ASC"
        return await self.query_all(
            f"SELECT {_TASK_FIELDS} FROM Task "
            f"WHERE OwnerId = {_soql_quote(owner_id)} AND IsClosed = false "
            f"AND ActivityDate < {_soql_date(today)} "
            f"ORDER BY ActivityDate {order}, CreatedDate {order} "
            f"LIMIT {int(limit)}"
        )

    async def find_today_tasks(self, owner_id: str, today: date, limit: int) -> list[dict[str, Any]]:
        return await self.query_all(
            f"SELECT {_TASK_FIELDS} FROM Task "
            f"WHERE OwnerId = {_soql_quote(owner_id)} AND IsClosed = false "
            f"AND ActivityDate = {_soql_date(today)} "
            "ORDER BY Priority ASC, CreatedDate ASC "
            f"LIMIT {int(limit)}"
        )

    # ------------------------------------------------------------------ #
    #  URLs                                                               #
    # ------------------------------------------------------------------ #
    def record_url(self, sobject: str, record_id: str) -> str:
        return f"{self._links_base()}/lightning/r/{sobject}/{record_id}/view"

    def tasks_home_url(self) -> str:
        return f"{self._links_base()}/lightning/o/Task/home"

    def _links_base(self) -> str:
        # La instancia que devuelve el login es la real; SALESFORCE_BASE_URL
        # puede haber quedado con el valor de ejemplo del .env.
        return self._session.instance_url if self._session else self._base

    # ------------------------------------------------------------------ #
    #  HTTP / login                                                       #
    # ------------------------------------------------------------------ #
    async def query_all(self, soql: str) -> list[dict[str, Any]]:
        response = await self._request(
            "GET",
            f"/services/data/{self._api_version}/query",
            params={"q": soql},
        )
        payload = response.json()
        records = payload.get("records", [])

        next_url = payload.get("nextRecordsUrl")
        while next_url:
            response = await self._request("GET", next_url)
            payload = response.json()
            records.extend(payload.get("records", []))
            next_url = payload.get("nextRecordsUrl")

        return records

    async def test_connection(self) -> bool:
        await self._ensure_session()
        return True

    async def _ensure_session(self) -> SalesforceSession:
        if self._session:
            return self._session

        if settings.salesforce_client_id and settings.salesforce_client_secret:
            self._session = await self._login_with_oauth_password_flow()
        else:
            self._session = await self._login_with_soap()

        return self._session

    async def _login_with_oauth_password_flow(self) -> SalesforceSession:
        if not settings.salesforce_username or not settings.salesforce_password:
            raise SalesforceAuthError("Faltan credenciales Salesforce.")

        password = settings.salesforce_password + settings.salesforce_security_token
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                f"{self._login_url}/services/oauth2/token",
                data={
                    "grant_type": "password",
                    "client_id": settings.salesforce_client_id,
                    "client_secret": settings.salesforce_client_secret,
                    "username": settings.salesforce_username,
                    "password": password,
                },
            )

        if response.status_code >= 400:
            logger.error("OAuth Salesforce fallo con status=%s", response.status_code)
            raise SalesforceAuthError("No se pudo autenticar con Salesforce OAuth.")

        payload = response.json()
        return SalesforceSession(
            access_token=payload["access_token"],
            instance_url=payload.get("instance_url", self._base).rstrip("/"),
        )

    async def _login_with_soap(self) -> SalesforceSession:
        if not settings.salesforce_username or not settings.salesforce_password:
            raise SalesforceAuthError("Faltan credenciales Salesforce.")

        password = settings.salesforce_password + settings.salesforce_security_token
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<env:Envelope xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xmlns:env="http://schemas.xmlsoap.org/soap/envelope/">'
            "<env:Body>"
            '<n1:login xmlns:n1="urn:partner.soap.sforce.com">'
            f"<n1:username>{escape(settings.salesforce_username)}</n1:username>"
            f"<n1:password>{escape(password)}</n1:password>"
            "</n1:login>"
            "</env:Body>"
            "</env:Envelope>"
        )

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                f"{self._login_url}/services/Soap/u/{self._api_version.lstrip('v')}",
                content=body,
                headers={
                    "Content-Type": "text/xml; charset=UTF-8",
                    "SOAPAction": "login",
                },
            )

        if response.status_code >= 400:
            logger.error("SOAP Salesforce fallo con status=%s", response.status_code)
            raise SalesforceAuthError("No se pudo autenticar con Salesforce SOAP.")

        root = ET.fromstring(response.text)
        session_id = self._find_xml_text(root, "sessionId")
        server_url = self._find_xml_text(root, "serverUrl")
        if not session_id or not server_url:
            fault = self._find_xml_text(root, "faultstring") or "Respuesta SOAP invalida."
            logger.error("SOAP Salesforce sin sessionId/serverUrl: %s", fault)
            raise SalesforceAuthError(f"No se pudo autenticar con Salesforce: {fault}")

        instance_url = server_url.split("/services/Soap/")[0].rstrip("/")
        return SalesforceSession(access_token=session_id, instance_url=instance_url)

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        session = await self._ensure_session()
        url = path if path.startswith("http") else f"{session.instance_url}{path}"
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {session.access_token}"

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.request(method, url, headers=headers, **kwargs)

        if response.status_code == 401:
            self._session = None
            session = await self._ensure_session()
            headers["Authorization"] = f"Bearer {session.access_token}"
            url = path if path.startswith("http") else f"{session.instance_url}{path}"
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.request(method, url, headers=headers, **kwargs)

        if response.status_code >= 400:
            logger.error(
                "Salesforce API error status=%s body=%s",
                response.status_code,
                response.text[:500],
            )
            raise SalesforceApiError(f"Salesforce API error {response.status_code}")

        return response

    @staticmethod
    def _find_xml_text(root: ET.Element, tag_name: str) -> Optional[str]:
        for element in root.iter():
            if element.tag.endswith(tag_name):
                return element.text
        return None


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]
