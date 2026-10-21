"""Arma los mensajes de Slack (texto de notificacion + blocks) a partir de
los formatos de message_templates.json.

Aqui no hay textos: todo lo que ve el usuario sale del JSON. Este modulo solo
reemplaza las variables {asi}, escapa los datos que vienen de Salesforce y
reparte las lineas en bloques que respeten los limites de Slack.
"""

import logging
import string
from datetime import date, datetime
from typing import Callable, Iterable, Optional

from services.models import ExecutiveReport, Person, TaskItem

logger = logging.getLogger(__name__)

# Limites de Slack Block Kit: texto de un section = 3000 chars, header = 150.
_SECTION_LIMIT = 2900
_HEADER_LIMIT = 150

Message = tuple[str, list[dict]]


class _KeepMissing(dict):
    """Una variable que no existe se deja tal cual ({asi}) en vez de romper
    el envio, para que un error de tipeo en el JSON se note en el mensaje."""

    def __missing__(self, key: str) -> str:
        logger.warning("Variable {%s} no existe en este mensaje (revisar message_templates.json)", key)
        return "{" + key + "}"


def render(template: str, **values) -> str:
    try:
        return string.Formatter().vformat(template, (), _KeepMissing(values))
    except (ValueError, IndexError) as error:
        logger.error("Formato invalido en message_templates.json: %r (%s)", template, error)
        return template


def slack_escape(value: str) -> str:
    """Escapa los caracteres de control de mrkdwn en datos de Salesforce
    (un asunto con '<' o '&' rompe el mensaje o el enlace)."""
    return (value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _link_text(value: str) -> str:
    # Dentro de <url|texto> un '|' extra o '>' cortaria el enlace.
    return slack_escape(value).replace("|", "¦")


class MessageBuilder:
    def __init__(
        self,
        templates: dict,
        today: date,
        now: datetime,
        record_url: Callable[[str, str], str],
        tasks_url: str,
    ):
        self.t = templates
        self.today = today
        self.now = now
        self.record_url = record_url
        self.tasks_url = tasks_url
        self.date_fmt = templates.get("formato_fecha", "%d/%m/%Y")
        self.datetime_fmt = templates.get("formato_fecha_hora", "%d/%m/%Y %H:%M")

    # ------------------------------------------------------------------ #
    #  Ejecutivo                                                          #
    # ------------------------------------------------------------------ #
    def executive_message(self, report: ExecutiveReport) -> Message:
        tpl = self.t["ejecutivo"]
        values = self._person_values(report.executive)
        values.update(
            total_vencidas=report.overdue_count,
            total_hoy=report.today_count,
            url_tareas=self.tasks_url,
        )

        blocks: list[dict] = [_header(render(tpl["header"], **values))]
        blocks += _sections([render(tpl["saludo"], **values), render(tpl["resumen"], **values)])
        blocks.append({"type": "divider"})

        blocks += self._task_section(
            title=render(tpl["titulo_vencidas"], **values),
            empty=render(tpl["sin_vencidas"], **values),
            tasks=report.overdue_tasks,
            total=report.overdue_count,
            item_template=self.t["actividad"]["item_vencida"],
        )
        blocks += self._task_section(
            title=render(tpl["titulo_hoy"], **values),
            empty=render(tpl["sin_hoy"], **values),
            tasks=report.today_tasks,
            total=report.today_count,
            item_template=self.t["actividad"]["item_hoy"],
        )

        blocks.append({"type": "divider"})
        blocks.append(_context(render(tpl["pie"], **values)))
        return render(tpl["texto_notificacion"], **values), blocks

    def _task_section(
        self,
        title: str,
        empty: str,
        tasks: list[TaskItem],
        total: int,
        item_template: str,
    ) -> list[dict]:
        if total == 0:
            return _sections([empty])

        act = self.t["actividad"]
        lines = [title] + [render(item_template, **self._task_values(task)) for task in tasks]
        remaining = total - len(tasks)
        if remaining > 0:
            lines.append(render(act["mas_items"], restantes=remaining, url_tareas=self.tasks_url))
        return _sections(["\n".join(lines)])

    def _task_values(self, task: TaskItem) -> dict:
        act = self.t["actividad"]
        days = (self.today - task.activity_date).days if task.activity_date else 0
        days_label = act["dias_singular"] if days == 1 else render(act["dias_plural"], n=days)
        return {
            "asunto": _link_text(task.subject) or act["sin_asunto"],
            "url": self.record_url("Task", task.id),
            "relacionado": slack_escape(task.related) or act["sin_relacionado"],
            "fecha_vencimiento": task.activity_date.strftime(self.date_fmt) if task.activity_date else "",
            "dias_vencida": days_label,
            "estado": slack_escape(task.status),
            "prioridad": slack_escape(task.priority),
            "tipo": slack_escape(task.type),
        }

    # ------------------------------------------------------------------ #
    #  Lider                                                              #
    # ------------------------------------------------------------------ #
    def leader_message(
        self,
        leader: Person,
        members: list[ExecutiveReport],
        hide_members_without_overdue: bool = False,
        max_tasks_per_member: int = 0,
    ) -> Message:
        tpl = self.t["lider"]
        values = self._person_values(leader)
        values.update(
            total_vencidas=sum(m.overdue_count for m in members),
            total_hoy=sum(m.today_count for m in members),
            total_miembros=len(members),
        )

        blocks: list[dict] = [_header(render(tpl["header"], **values))]
        blocks += _sections([render(tpl["saludo"], **values), render(tpl["total_equipo"], **values)])
        blocks.append({"type": "divider"})

        ordered = sorted(members, key=lambda m: (-m.overdue_count, m.executive.name))
        if hide_members_without_overdue:
            ordered = [m for m in ordered if m.overdue_count > 0]

        if values["total_vencidas"] == 0 and not ordered:
            blocks += _sections([render(tpl["sin_pendientes"], **values)])
        else:
            lines: list[str] = []
            for member in ordered:
                if lines and max_tasks_per_member > 0:
                    lines.append("")  # separa a cada ejecutivo cuando lleva detalle
                lines += self._member_lines(member, max_tasks_per_member)
            # Todas las lineas en un solo texto: un section por miembro
            # pasaria el limite de 50 blocks de Slack con equipos grandes.
            blocks += _sections(["\n".join(lines)])

        blocks.append({"type": "divider"})
        blocks.append(_context(render(tpl["pie"], **values)))
        return render(tpl["texto_notificacion"], **values), blocks

    def _member_lines(self, member: ExecutiveReport, max_tasks: int) -> list[str]:
        """Linea con los totales del ejecutivo y, si max_tasks > 0, un breve
        detalle de sus vencidas mas recientes y sus tareas de hoy."""
        tpl = self.t["lider"]
        lines = [
            render(
                tpl["item_miembro"],
                ejecutivo=slack_escape(member.executive.name),
                email_ejecutivo=member.executive.email,
                total_vencidas=member.overdue_count,
                total_hoy=member.today_count,
            )
        ]
        if max_tasks <= 0:
            return lines

        for tasks, total, item_key, more_key in (
            (member.overdue_tasks, member.overdue_count, "item_vencida_miembro", "mas_vencidas_miembro"),
            (member.today_tasks, member.today_count, "item_hoy_miembro", "mas_hoy_miembro"),
        ):
            shown = tasks[:max_tasks]
            lines += [render(tpl[item_key], **self._task_values(task)) for task in shown]
            if total > len(shown):
                lines.append(render(tpl[more_key], restantes=total - len(shown)))
        return lines

    # ------------------------------------------------------------------ #
    #  Canal (resumen general por lider)                                  #
    # ------------------------------------------------------------------ #
    def channel_summary_message(
        self,
        groups: list[tuple[Optional[Person], list[ExecutiveReport]]],
        show_members: bool = True,
        max_members: int = 0,
    ) -> Message:
        """groups: (lider, ejecutivos a su cargo); lider None = sin lider."""
        tpl = self.t["canal"]
        members = [m for _, team in groups for m in team]
        values = {
            "fecha": self.today.strftime(self.date_fmt),
            "fecha_hora": self.now.strftime(self.datetime_fmt),
            "total_vencidas": sum(m.overdue_count for m in members),
            "total_hoy": sum(m.today_count for m in members),
            "total_lideres": sum(1 for leader, _ in groups if leader),
            "total_ejecutivos": len(members),
        }

        blocks: list[dict] = [_header(render(tpl["header"], **values))]
        blocks += _sections([render(tpl["intro"], **values), render(tpl["total_general"], **values)])
        blocks.append({"type": "divider"})

        def team_overdue(team: list[ExecutiveReport]) -> int:
            return sum(m.overdue_count for m in team)

        # Mas vencidas primero; "sin lider" siempre al final.
        ordered = sorted(groups, key=lambda g: (g[0] is None, -team_overdue(g[1]), g[0].name if g[0] else ""))
        lines: list[str] = []
        for leader, team in ordered:
            if lines and show_members:
                lines.append("")
            lines.append(
                render(
                    tpl["item_lider"],
                    lider=slack_escape(leader.name) if leader else tpl["sin_lider"],
                    email_lider=leader.email if leader else "",
                    total_vencidas=team_overdue(team),
                    total_hoy=sum(m.today_count for m in team),
                    total_miembros=len(team),
                )
            )
            if show_members:
                ranked = sorted(team, key=lambda m: (-m.overdue_count, m.executive.name))
                shown = ranked[:max_members] if max_members > 0 else ranked
                for member in shown:
                    lines.append(
                        render(
                            tpl["item_miembro"],
                            ejecutivo=slack_escape(member.executive.name),
                            email_ejecutivo=member.executive.email,
                            total_vencidas=member.overdue_count,
                            total_hoy=member.today_count,
                        )
                    )
                if len(ranked) > len(shown):
                    lines.append(render(tpl.get("mas_miembros", "      _…y {restantes} más_"), restantes=len(ranked) - len(shown)))
        if lines:
            blocks += _sections(["\n".join(lines)])

        blocks.append({"type": "divider"})
        blocks.append(_context(render(tpl["pie"], **values)))
        return render(tpl["texto_notificacion"], **values), blocks

    # ------------------------------------------------------------------ #
    #  Modo prueba                                                        #
    # ------------------------------------------------------------------ #
    def with_test_banner(self, message: Message, recipient: Person, kind: str) -> Message:
        tpl = self.t["modo_prueba"]
        banner = render(
            tpl["banner"],
            destinatario=slack_escape(recipient.name),
            email_destinatario=recipient.email or "sin email",
            tipo_mensaje=tpl.get(f"tipo_{kind}", kind),
        )
        text, blocks = message
        return f"[PRUEBA → {recipient.name}] {text}", [_context(banner)] + blocks

    def with_channel_test_banner(self, message: Message, channel_id: str) -> Message:
        tpl = self.t["modo_prueba"]
        banner = render(tpl["banner_canal"], canal=f"<#{channel_id}>" if channel_id else "(sin configurar)")
        text, blocks = message
        return f"[PRUEBA → canal] {text}", [_context(banner)] + blocks

    def _person_values(self, person: Person) -> dict:
        return {
            "nombre": slack_escape(person.first_name),
            "nombre_completo": slack_escape(person.name),
            "email": person.email,
            "fecha": self.today.strftime(self.date_fmt),
            "fecha_hora": self.now.strftime(self.datetime_fmt),
        }


# ---------------------------------------------------------------------- #
#  Helpers de Block Kit                                                   #
# ---------------------------------------------------------------------- #
def _header(text: str) -> dict:
    if len(text) > _HEADER_LIMIT:
        text = text[: _HEADER_LIMIT - 1] + "…"
    return {"type": "header", "text": {"type": "plain_text", "text": text, "emoji": True}}


def _context(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text[:_SECTION_LIMIT]}]}


def _sections(texts: Iterable[str]) -> list[dict]:
    """Un section por texto; si un texto pasa el limite de Slack se parte
    por lineas en varios sections consecutivos."""
    blocks = []
    for text in texts:
        for chunk in split_lines(text, _SECTION_LIMIT):
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})
    return blocks


def split_lines(text: str, limit: int) -> list[str]:
    chunks: list[str] = []
    current: Optional[str] = None
    for line in text.split("\n"):
        line = line if len(line) <= limit else line[: limit - 1] + "…"
        candidate = line if current is None else f"{current}\n{line}"
        if len(candidate) > limit and current is not None:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def blocks_to_preview(text: str, blocks: list[dict]) -> str:
    """Version legible en consola de un mensaje (para --dry-run)."""
    lines = [f"[notificacion] {text}"]
    for block in blocks:
        if block["type"] == "divider":
            lines.append("-" * 60)
        elif block["type"] == "header":
            lines.append(f"## {block['text']['text']}")
        elif block["type"] == "section":
            lines.append(block["text"]["text"])
        elif block["type"] == "context":
            lines.append(" ".join(el.get("text", "") for el in block["elements"]))
    return "\n".join(lines)
