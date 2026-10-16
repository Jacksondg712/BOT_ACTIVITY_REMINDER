from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional


@dataclass
class Person:
    id: str
    name: str
    first_name: str
    email: str


@dataclass
class TaskItem:
    id: str
    subject: str
    activity_date: Optional[date]
    status: str
    priority: str
    type: str
    related: str

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "TaskItem":
        related_names = [
            (record.get(rel) or {}).get("Name")
            for rel in ("Who", "What")
        ]
        raw_date = record.get("ActivityDate")
        return cls(
            id=record["Id"],
            subject=record.get("Subject") or "",
            activity_date=date.fromisoformat(raw_date) if raw_date else None,
            status=record.get("Status") or "",
            priority=record.get("Priority") or "",
            type=record.get("Type") or "",
            related=" · ".join(name for name in related_names if name),
        )


@dataclass
class ExecutiveReport:
    executive: Person
    leader: Optional[Person]
    overdue_count: int = 0
    today_count: int = 0
    overdue_tasks: list[TaskItem] = field(default_factory=list)
    today_tasks: list[TaskItem] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return self.overdue_count == 0 and self.today_count == 0


def person_from_user(record: dict[str, Any]) -> Person:
    name = record.get("Name") or ""
    return Person(
        id=record["Id"],
        name=name,
        first_name=record.get("FirstName") or name.split(" ")[0],
        email=(record.get("Email") or "").lower(),
    )


def leader_from_user(record: dict[str, Any]) -> Optional[Person]:
    manager = record.get("Manager") or {}
    if not record.get("ManagerId") or not manager:
        return None
    # Un Manager desactivado en Salesforce ya no recibe el resumen.
    if manager.get("IsActive") is False:
        return None
    name = manager.get("Name") or ""
    return Person(
        id=record["ManagerId"],
        name=name,
        first_name=manager.get("FirstName") or name.split(" ")[0],
        email=(manager.get("Email") or "").lower(),
    )
