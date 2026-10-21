"""Flujo completo del recordatorio:

1. Trae de Salesforce a los ejecutivos y a su lider (Manager del usuario):
   - Ejecutivotest1..3 del .env, si TEST_MODE=true y estan configurados.
   - Si no: usuarios activos con Title en executive_titles o, si esa lista
     esta vacia, todos los del rol/perfil 'b2b_keyword' (reminder_config.json).
     Se quitan los TITULOS_BLOQUEADOS del .env, quien no tenga lider y
     quien tenga personas a cargo (cuenta solo como lider).
2. Cuenta sus Tasks abiertas vencidas y del dia, y trae el detalle de las
   primeras N de cada una.
3. Arma los mensajes con message_templates.json y los envia:
   - Produccion: DM a cada ejecutivo y a cada lider.
   - Modo prueba: TODO va al canal de pruebas (SLACK_TEST_CHANNEL_ID) y por
     DM a Ejecutivotestsend1, con un aviso de a quien le habria llegado.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from config import message_templates, reminder_config, settings
from services.models import ExecutiveReport, Person, TaskItem, leader_from_user, person_from_user
from services.salesforce_client import SalesforceClient
from services.slack_service import SlackService
from utils.business_days import non_business_reason
from utils.message_builder import Message, MessageBuilder, blocks_to_preview

logger = logging.getLogger(__name__)

_DETAIL_CONCURRENCY = 5


@dataclass
class RunSummary:
    executives: int = 0
    leaders: int = 0
    sent: int = 0
    failed: int = 0
    skipped_empty: int = 0
    missing_users: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def local_now() -> datetime:
    return datetime.now(ZoneInfo(reminder_config.get("timezone", "America/Bogota")))


def skip_reason(now: Optional[datetime] = None) -> Optional[str]:
    """Por que no se debe enviar hoy ('sabado', 'domingo' o el festivo), o
    None si toca enviar. Respeta schedule.business_days_only."""
    if not reminder_config.get("schedule", {}).get("business_days_only", True):
        return None
    return non_business_reason((now or local_now()).date())


# ---------------------------------------------------------------------- #
#  1-2. Extraccion de Salesforce                                          #
# ---------------------------------------------------------------------- #
def filter_executives(
    users: list[dict],
    blocked_titles: set[str],
    require_leader: bool,
    exclude_leaders: bool = False,
) -> list[dict]:
    """Quita los titulos bloqueados, (opcional) a quien no tiene un lider
    activo en Salesforce y (opcional) a quien tiene personas a cargo: ese
    cuenta solo como lider, no como ejecutivo del equipo de su jefe."""
    leader_ids = {user.get("ManagerId") for user in users} if exclude_leaders else set()
    kept = []
    for user in users:
        if user["Id"] in leader_ids:
            continue
        if (user.get("Title") or "").strip().lower() in blocked_titles:
            continue
        if require_leader and not leader_from_user(user):
            continue
        kept.append(user)
    return kept


async def find_executives(sf: SalesforceClient) -> list[dict]:
    titles = reminder_config.get("executive_titles") or []
    if titles:
        users = await sf.find_active_users_by_titles(titles)
    else:
        users = await sf.find_active_users_by_keyword(reminder_config.get("b2b_keyword", "B2B"))
    kept = filter_executives(
        users,
        settings.blocked_titles,
        bool(reminder_config.get("exclude_without_leader", True)),
        bool(reminder_config.get("leaders_are_not_executives", True)),
    )
    logger.info(
        "Ejecutivos: %s usuarios encontrados, %s despues de quitar titulos bloqueados, sin lider y lideres",
        len(users),
        len(kept),
    )
    return kept


async def collect_reports(
    sf: SalesforceClient,
    now: datetime,
    summary: RunSummary,
    with_details: bool = True,
) -> list[ExecutiveReport]:
    """`with_details=False` solo cuenta (para el resumen del canal no hace
    falta traer el detalle de cada tarea)."""
    today = now.date()

    if settings.use_test_executives:
        emails = settings.test_executive_emails
        if not emails:
            raise RuntimeError("TEST_MODE=true pero no hay Ejecutivotest1..3 configurados en .env")
        users = await sf.find_active_users_by_emails(emails)
        found = {(user.get("Email") or "").lower() for user in users}
        summary.missing_users = [email for email in emails if email not in found]
        for email in summary.missing_users:
            logger.warning("Ejecutivo de prueba %s no existe o no esta activo en Salesforce", email)
    else:
        users = await find_executives(sf)

    if not users:
        logger.warning("No se encontraron ejecutivos para procesar.")
        return []

    counts = await sf.count_open_tasks_by_owner([user["Id"] for user in users], today)
    max_overdue = int(reminder_config.get("max_overdue_items", 10))
    max_today = int(reminder_config.get("max_today_items", 15))
    newest_first = reminder_config.get("overdue_order", "newest_first") != "oldest_first"
    semaphore = asyncio.Semaphore(_DETAIL_CONCURRENCY)

    async def build(user: dict) -> ExecutiveReport:
        user_counts = counts.get(user["Id"], {})
        report = ExecutiveReport(
            executive=person_from_user(user),
            leader=leader_from_user(user),
            overdue_count=user_counts.get("overdue", 0),
            today_count=user_counts.get("today", 0),
        )
        if not with_details:
            return report
        async with semaphore:
            if report.overdue_count and max_overdue > 0:
                records = await sf.find_overdue_tasks(user["Id"], today, max_overdue, newest_first)
                report.overdue_tasks = [TaskItem.from_record(r) for r in records]
            if report.today_count and max_today > 0:
                records = await sf.find_today_tasks(user["Id"], today, max_today)
                report.today_tasks = [TaskItem.from_record(r) for r in records]
        logger.info(
            "Ejecutivo %s: %s vencidas, %s para hoy (lider: %s)",
            report.executive.name,
            report.overdue_count,
            report.today_count,
            report.leader.name if report.leader else "sin lider",
        )
        return report

    return list(await asyncio.gather(*(build(user) for user in users)))


def group_by_leader(reports: list[ExecutiveReport]) -> list[tuple[Person, list[ExecutiveReport]]]:
    groups: dict[str, tuple[Person, list[ExecutiveReport]]] = {}
    for report in reports:
        if not report.leader:
            continue
        groups.setdefault(report.leader.id, (report.leader, []))[1].append(report)
    return sorted(groups.values(), key=lambda group: group[0].name)


async def leader_groups(
    sf: SalesforceClient, reports: list[ExecutiveReport]
) -> list[tuple[Person, list[ExecutiveReport]]]:
    if settings.use_test_executives:
        return await test_leader_groups(sf, reports)
    return group_by_leader(reports)


async def test_leader_groups(
    sf: SalesforceClient, reports: list[ExecutiveReport]
) -> list[tuple[Person, list[ExecutiveReport]]]:
    """Modo prueba: todos los ejecutivos de prueba quedan a cargo de
    Ejecutivotestsend1, que hace de lider (no su Manager real)."""
    email = settings.ejecutivotestsend1.strip().lower()
    if not email or not reports:
        return []
    users = await sf.find_active_users_by_emails([email])
    if users:
        leader = person_from_user(users[0])
    else:
        alias = email.split("@")[0]
        leader = Person(id="test-leader", name=alias, first_name=alias.split(".")[0].capitalize(), email=email)
    return [(leader, reports)]


# ---------------------------------------------------------------------- #
#  3. Envio                                                               #
# ---------------------------------------------------------------------- #
class Dispatcher:
    """Decide a donde va cada mensaje segun TEST_MODE y lo envia (o lo
    imprime, con dry_run)."""

    def __init__(self, slack: SlackService, builder: MessageBuilder, dry_run: bool):
        self.slack = slack
        self.builder = builder
        self.dry_run = dry_run
        self.delay = float(reminder_config.get("seconds_between_messages", 1.2))
        self._slack_ids: dict[str, Optional[str]] = {}

    async def deliver(self, message: Message, recipient: Person, kind: str, summary: RunSummary) -> None:
        if settings.test_mode:
            message = self.builder.with_test_banner(message, recipient, kind)
            targets = []
            if settings.slack_test_channel_id:
                targets.append(("canal de pruebas", "channel", settings.slack_test_channel_id))
            if settings.ejecutivotestsend1:
                targets.append(("Ejecutivotestsend1", "email", settings.ejecutivotestsend1.strip()))
            if not targets:
                logger.error("TEST_MODE=true pero no hay SLACK_TEST_CHANNEL_ID ni Ejecutivotestsend1 en .env")
        else:
            targets = [(recipient.name, "email", recipient.email)]

        for label, target_type, target in targets:
            if self.dry_run:
                print(f"\n===== [DRY-RUN] {kind} de {recipient.name} → {label} ({target}) =====")
                print(blocks_to_preview(*message))
                continue

            ok = await self._send(target_type, target, message)
            if ok:
                summary.sent += 1
                logger.info("[SLACK] %s de %s enviado a %s", kind, recipient.name, label)
            else:
                summary.failed += 1
                logger.error("[SLACK] No se pudo enviar %s de %s a %s (%s)", kind, recipient.name, label, target)
            await asyncio.sleep(self.delay)

    async def deliver_to_channel(self, message: Message, channel_id: str, summary: RunSummary) -> None:
        """Mensaje de canal. En modo prueba va solo al canal de pruebas."""
        if settings.test_mode:
            message = self.builder.with_channel_test_banner(message, channel_id)
            label, target = "canal de pruebas", settings.slack_test_channel_id
        else:
            label, target = "canal de resumen", channel_id
        if not target:
            summary.failed += 1
            logger.error(
                "No hay canal para el resumen: configurar %s",
                "SLACK_TEST_CHANNEL_ID en .env" if settings.test_mode else "channel_summary.channel_id en reminder_config.json",
            )
            return

        if self.dry_run:
            print(f"\n===== [DRY-RUN] resumen de canal → {label} ({target}) =====")
            print(blocks_to_preview(*message))
            return

        if await self.slack.post_message(target, *message):
            summary.sent += 1
            logger.info("[SLACK] resumen de canal enviado a %s", label)
        else:
            summary.failed += 1
            logger.error("[SLACK] No se pudo enviar el resumen de canal a %s (%s)", label, target)

    async def _send(self, target_type: str, target: str, message: Message) -> bool:
        text, blocks = message
        if target_type == "channel":
            return await self.slack.post_message(target, text, blocks)

        if not target:
            return False
        if target not in self._slack_ids:
            self._slack_ids[target] = await self.slack.find_user_id_by_email(target)
        slack_id = self._slack_ids[target]
        if not slack_id:
            logger.warning("[SLACK] %s no tiene usuario de Slack con ese email", target)
            return False
        return await self.slack.send_dm(slack_id, text, blocks)


async def run_reminder(
    dry_run: bool = False,
    include_executives: bool = True,
    include_leaders: Optional[bool] = None,
    include_channel: Optional[bool] = None,
    sf: Optional[SalesforceClient] = None,
    slack: Optional[SlackService] = None,
) -> RunSummary:
    if include_leaders is None:
        include_leaders = bool(reminder_config.get("leaders", {}).get("enabled"))
    channel_cfg = reminder_config.get("channel_summary", {})
    if include_channel is None:
        include_channel = bool(channel_cfg.get("enabled"))
    if not settings.slack_notifications_enabled and not dry_run:
        logger.warning("SLACK_NOTIFICATIONS_ENABLED=false: se ejecuta en modo dry-run (sin enviar).")
        dry_run = True

    sf = sf or SalesforceClient()
    slack = slack or SlackService()
    now = local_now()
    summary = RunSummary()

    logger.info(
        "Recordatorio %s — modo %s%s",
        now.strftime("%Y-%m-%d %H:%M"),
        "PRUEBA" if settings.test_mode else "PRODUCCION",
        " (dry-run)" if dry_run else "",
    )

    reports = await collect_reports(sf, now, summary, with_details=include_executives or include_leaders)
    summary.executives = len(reports)

    tasks_url = (reminder_config.get("salesforce_tasks_url") or "").strip() or sf.tasks_home_url()
    builder = MessageBuilder(message_templates, now.date(), now, sf.record_url, tasks_url)
    dispatcher = Dispatcher(slack, builder, dry_run)

    if include_executives:
        send_empty = bool(reminder_config.get("send_when_empty", True))
        for report in reports:
            if report.is_empty and not send_empty:
                summary.skipped_empty += 1
                continue
            await dispatcher.deliver(builder.executive_message(report), report.executive, "ejecutivo", summary)

    if include_leaders:
        leaders_cfg = reminder_config.get("leaders", {})
        hide_zero = bool(leaders_cfg.get("hide_members_without_overdue"))
        max_tasks = int(leaders_cfg.get("max_tasks_per_member", 3))
        for leader, members in await leader_groups(sf, reports):
            summary.leaders += 1
            message = builder.leader_message(leader, members, hide_zero, max_tasks)
            await dispatcher.deliver(message, leader, "lider", summary)

    if include_channel and reports:
        groups: list[tuple[Optional[Person], list[ExecutiveReport]]] = list(await leader_groups(sf, reports))
        grouped = {id(m) for _, team in groups for m in team}
        without_leader = [r for r in reports if id(r) not in grouped]
        if without_leader:
            groups.append((None, without_leader))
        message = builder.channel_summary_message(
            groups,
            bool(channel_cfg.get("show_members", True)),
            int(channel_cfg.get("max_members_per_leader", 0)),
        )
        await dispatcher.deliver_to_channel(message, (channel_cfg.get("channel_id") or "").strip(), summary)

    logger.info("Resumen de la ejecucion: %s", summary.as_dict())
    return summary
