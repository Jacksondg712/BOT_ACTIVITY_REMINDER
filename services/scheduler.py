"""Envio programado (python main.py --schedule).

Revisa cada minuto si ya toca alguna de las horas de reminder_config.json →
schedule.times. Guarda en schedule_state.json que horas ya se enviaron hoy
para no duplicar si el bot se reinicia.
"""

import asyncio
import json
import logging
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Optional

from config import reminder_config
from services.reminder import local_now, run_reminder, skip_reason

logger = logging.getLogger(__name__)

_STATE_PATH = Path(__file__).resolve().parent.parent / "schedule_state.json"
_CHECK_SECONDS = 60


def _load_state() -> dict:
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    _STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def due_slot(now: datetime, schedule: dict, sent: list[str]) -> Optional[str]:
    """La hora programada que toca enviar ahora (ya paso, dentro del margen
    de catch-up y no enviada todavia hoy), o None."""
    catchup = timedelta(minutes=int(schedule.get("catchup_minutes", 60)))
    for slot in sorted(schedule.get("times", [])):
        if slot in sent:
            continue
        scheduled = datetime.combine(now.date(), time.fromisoformat(slot), tzinfo=now.tzinfo)
        if scheduled <= now <= scheduled + catchup:
            return slot
    return None


class ReminderScheduler:
    def __init__(self):
        self._running = True

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        schedule = reminder_config.get("schedule", {})
        logger.info(
            "Scheduler activo: horas %s (%s), solo dias habiles=%s",
            schedule.get("times"),
            reminder_config.get("timezone"),
            schedule.get("business_days_only", True),
        )
        while self._running:
            try:
                await self._tick(schedule)
            except Exception:
                logger.exception("Error en el ciclo del scheduler")
            await asyncio.sleep(_CHECK_SECONDS)

    async def _tick(self, schedule: dict) -> None:
        now = local_now()
        day_key = now.date().isoformat()

        if skip_reason(now):
            return

        state = _load_state()
        sent_today = state.get(day_key, [])
        slot = due_slot(now, schedule, sent_today)
        if not slot:
            return

        logger.info("Toca el envio de las %s", slot)
        await run_reminder()
        # Solo se conserva el dia actual para que el archivo no crezca.
        _save_state({day_key: sent_today + [slot]})
