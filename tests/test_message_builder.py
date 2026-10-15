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
