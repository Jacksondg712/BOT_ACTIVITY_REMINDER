import json
from datetime import date, datetime
from pathlib import Path

from services.models import ExecutiveReport, Person, TaskItem
from utils.message_builder import MessageBuilder, render, slack_escape, split_lines

TEMPLATES = json.loads(
    (Path(__file__).resolve().parent.parent / "message_templates.json").read_text(encoding="utf-8")
)
TODAY = date(2026, 10, 14)


def _builder(templates=TEMPLATES):
    return MessageBuilder(
        templates,
        TODAY,
        datetime(2026, 10, 14, 8, 0),
        lambda sobject, record_id: f"https://sf/{sobject}/{record_id}",
        "https://sf/tasks",
    )


def _task(task_id="00T1", subject="Llamar", days_ago=3, related="ACME"):
    return TaskItem(
        id=task_id,
        subject=subject,
        activity_date=date.fromordinal(TODAY.toordinal() - days_ago),
        status="Not Started",
        priority="Normal",
        type="",
        related=related,
    )


def _report(overdue=0, today=0, overdue_tasks=None, today_tasks=None):
    return ExecutiveReport(
        executive=Person("005A", "Diana Villanueva", "Diana", "diana@x.com"),
        leader=Person("005L", "Edna Guerrero", "Edna", "edna@x.com"),
        overdue_count=overdue,
        today_count=today,
        overdue_tasks=overdue_tasks or [],
        today_tasks=today_tasks or [],
    )


def _all_text(blocks):
    parts = []
    for block in blocks:
        if "text" in block:
            parts.append(block["text"]["text"])
        for element in block.get("elements", []):
            parts.append(element["text"])
    return "\n".join(parts)


def test_render_keeps_unknown_variables():
    assert render("Hola {nombre} {no_existe}", nombre="Ana") == "Hola Ana {no_existe}"


def test_slack_escape():
    assert slack_escape("A & <B>") == "A &amp; &lt;B&gt;"


def test_executive_message_lists_tasks_and_remaining_count():
    tasks = [_task(f"00T{i}", days_ago=i + 1) for i in range(3)]
    text, blocks = _builder().executive_message(_report(overdue=10, today=0, overdue_tasks=tasks))
    body = _all_text(blocks)

    assert "10" in text
    assert "https://sf/Task/00T0" in body
    assert "hace 1 día" in body
    assert "hace 3 días" in body
    assert "…y 7 más" in body
    assert "No tienes tareas programadas para hoy." in body


def test_executive_message_when_everything_is_done():
    _, blocks = _builder().executive_message(_report())
    body = _all_text(blocks)
    assert "No tienes actividades vencidas" in body
    assert "No tienes tareas programadas para hoy." in body


def test_subject_with_link_breaking_characters_is_escaped():
    task = _task(subject="Cita <urgente> | cliente", related="A&B")
    _, blocks = _builder().executive_message(_report(overdue=1, overdue_tasks=[task]))
    body = _all_text(blocks)
    assert "|Cita &lt;urgente&gt; ¦ cliente>" in body
    assert "A&amp;B" in body


def test_templates_are_editable_from_json():
    templates = json.loads(json.dumps(TEMPLATES))
    templates["ejecutivo"]["header"] = "Pendientes de {nombre_completo}"
    _, blocks = _builder(templates).executive_message(_report())
    assert blocks[0]["text"]["text"] == "Pendientes de Diana Villanueva"


def test_leader_message_sorts_by_overdue():
    low = _report(overdue=2, today=1)
    high = _report(overdue=50, today=0)
    high.executive = Person("005B", "Grimaldis Lopez", "Grimaldis", "g@x.com")
    text, blocks = _builder().leader_message(low.leader, [low, high])
    body = _all_text(blocks)

    assert "52" in text
    assert body.index("Grimaldis Lopez") < body.index("Diana Villanueva")


def test_test_banner_is_prepended():
    message = _builder().executive_message(_report())
    recipient = Person("005A", "Diana Villanueva", "Diana", "diana@x.com")
    text, blocks = _builder().with_test_banner(message, recipient, "ejecutivo")
    assert text.startswith("[PRUEBA")
    assert blocks[0]["type"] == "context"
    assert "Diana Villanueva" in blocks[0]["elements"][0]["text"]


def test_split_lines_respects_limit():
    text = "\n".join("x" * 50 for _ in range(100))
    chunks = split_lines(text, 300)
    assert all(len(chunk) <= 300 for chunk in chunks)
    assert "\n".join(chunks) == text


def test_block_count_stays_under_slack_limit():
    tasks = [_task(f"00T{i}", subject="S" * 200, related="R" * 200) for i in range(25)]
    _, blocks = _builder().executive_message(
        _report(overdue=500, today=25, overdue_tasks=tasks, today_tasks=tasks)
    )
    assert len(blocks) <= 50
    for block in blocks:
        if block["type"] == "section":
            assert len(block["text"]["text"]) <= 3000


def test_leader_message_with_big_team_stays_under_slack_limit():
    members = []
    for i in range(120):
        member = _report(overdue=i, today=1)
        member.executive = Person(f"005{i}", f"Ejecutivo {i:03d}", "Ejecutivo", f"e{i}@x.com")
        members.append(member)
    _, blocks = _builder().leader_message(members[0].leader, members)

    assert len(blocks) <= 50
    body = _all_text(blocks)
    assert "Ejecutivo 000" in body and "Ejecutivo 119" in body
    for block in blocks:
        if block["type"] == "section":
            assert len(block["text"]["text"]) <= 3000


def test_leader_message_lists_brief_detail_per_member():
    overdue = [_task(f"00T{i}", subject=f"Vencida {i}", days_ago=i + 1) for i in range(5)]
    today = [_task("00H1", subject="Llamar hoy", days_ago=0)]
    member = _report(overdue=40, today=1, overdue_tasks=overdue, today_tasks=today)
    _, blocks = _builder().leader_message(member.leader, [member], max_tasks_per_member=3)
    body = _all_text(blocks)

    assert "Vencida 0" in body and "Vencida 2" in body
    assert "Vencida 3" not in body
    assert "…y 37 vencidas más" in body
    assert "Llamar hoy" in body
    assert "para hoy más" not in body


def test_leader_message_without_detail_shows_only_totals():
    member = _report(overdue=2, today=1, overdue_tasks=[_task(subject="Oculta")])
    _, blocks = _builder().leader_message(member.leader, [member], max_tasks_per_member=0)
    body = _all_text(blocks)
    assert "Diana Villanueva" in body
    assert "Oculta" not in body


def _member(name, overdue, today=0):
    member = _report(overdue=overdue, today=today)
    member.executive = Person(f"005{name}", name, name, f"{name}@x.com")
    return member


def test_channel_summary_groups_by_leader_and_sorts():
    small = (Person("005L1", "Lider Uno", "Lider", "l1@x.com"), [_member("Ana", 1)])
    big = (Person("005L2", "Lider Dos", "Lider", "l2@x.com"), [_member("Beto", 5, 2), _member("Caro", 20)])
    no_leader = (None, [_member("Dani", 50)])
    text, blocks = _builder().channel_summary_message([small, no_leader, big])
    body = _all_text(blocks)

    assert "76 vencidas y 2 para hoy" in text
    assert "Líderes: 2 · Ejecutivos: 4" in body
    assert "*Lider Dos* — equipo de 2: :red_circle: 25 vencidas" in body
    assert body.index("Lider Dos") < body.index("Lider Uno") < body.index("Sin líder asignado")
    assert body.index("Caro") < body.index("Beto")


def test_channel_summary_without_members_shows_one_line_per_leader():
    group = (Person("005L1", "Lider Uno", "Lider", "l1@x.com"), [_member("Ana", 3)])
    _, blocks = _builder().channel_summary_message([group], show_members=False)
    body = _all_text(blocks)
    assert "Lider Uno" in body
    assert "Ana" not in body


def test_channel_test_banner_names_production_channel():
    message = _builder().channel_summary_message([])
    text, blocks = _builder().with_channel_test_banner(message, "C123")
    assert text.startswith("[PRUEBA")
    assert "<#C123>" in blocks[0]["elements"][0]["text"]


def test_channel_summary_shows_top_members_by_overdue():
    leader = Person("005L", "Edna Guerrero", "Edna", "edna@x.com")
    team = []
    for i, overdue in enumerate([5, 50, 1, 30, 10]):
        report = _report(overdue=overdue)
        report.executive = Person(f"005{i}", f"Ejecutivo {overdue}", "E", f"e{i}@x.com")
        team.append(report)
    _, blocks = _builder().channel_summary_message([(leader, team)], show_members=True, max_members=3)
    body = _all_text(blocks)
    assert body.index("Ejecutivo 50") < body.index("Ejecutivo 30") < body.index("Ejecutivo 10")
    assert "Ejecutivo 5:" not in body and "Ejecutivo 1:" not in body
    assert "…y 2 ejecutivos más" in body
