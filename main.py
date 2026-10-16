"""BOT_ACTIVITY_REMINDER — recordatorio de actividades de Salesforce por Slack.

Uso:
    python main.py --dry-run      # muestra en consola los mensajes, NO envia nada
    python main.py                # envia el recordatorio una vez, ahora
    python main.py --schedule     # queda corriendo y envia a las horas de reminder_config.json
    python main.py --check        # solo verifica conexion a Salesforce y Slack

Filtros:
    --solo ejecutivos | lideres   # enviar solo un tipo de mensaje
    --forzar                      # enviar aunque hoy no sea dia habil
"""

import argparse
import asyncio
import logging
import sys

from config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bot_activity_reminder")

if settings.quiet_http_logs:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

# La consola de Windows (cp1252) no imprime emojis/acentos del --dry-run.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


async def check_connections() -> bool:
    from services.salesforce_client import SalesforceClient
    from services.slack_service import SlackService

    ok = True
    try:
        await SalesforceClient().test_connection()
        logger.info("Salesforce: conexion OK")
    except Exception as error:
        logger.error("Salesforce: fallo la conexion (%s)", error)
        ok = False

    slack = SlackService()
    auth = await slack.auth_test()
    if auth:
        logger.info("Slack: conexion OK como bot '%s' en '%s'", auth["bot"], auth["team"])
    else:
        ok = False

    if settings.test_mode:
        logger.info("Modo PRUEBA. Ejecutivos de prueba: %s", settings.test_executive_emails)
        logger.info("Canal de pruebas: %s", settings.slack_test_channel_id or "(no configurado)")
        if settings.ejecutivotestsend1:
            slack_id = await slack.find_user_id_by_email(settings.ejecutivotestsend1.strip())
            logger.info(
                "Ejecutivotestsend1 %s → %s",
                settings.ejecutivotestsend1,
                slack_id or "NO encontrado en Slack",
            )
        else:
            logger.warning("Ejecutivotestsend1 no esta configurado en .env (solo se enviara al canal)")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Recordatorio de actividades vencidas y del dia")
    parser.add_argument("--dry-run", action="store_true", help="mostrar los mensajes sin enviarlos")
    parser.add_argument("--schedule", action="store_true", help="quedar corriendo y enviar a las horas programadas")
    parser.add_argument("--check", action="store_true", help="verificar conexion a Salesforce y Slack")
    parser.add_argument("--solo", choices=["ejecutivos", "lideres"], help="enviar solo un tipo de mensaje")
    parser.add_argument("--forzar", action="store_true", help="enviar aunque hoy no sea dia habil")
    args = parser.parse_args()

    if args.check:
        return 0 if asyncio.run(check_connections()) else 1

    if args.schedule:
        from services.scheduler import ReminderScheduler

        try:
            asyncio.run(ReminderScheduler().run())
        except KeyboardInterrupt:
            logger.info("Scheduler detenido.")
        return 0

    from services.reminder import run_reminder, skip_reason

    # Sin --schedule (p. ej. desde el Programador de tareas de Windows) tambien
    # se respetan los dias habiles; --dry-run siempre muestra los mensajes.
    reason = None if (args.forzar or args.dry_run) else skip_reason()
    if reason:
        logger.info("Hoy no es dia habil (%s): no se envia nada. Usar --forzar para enviar igual.", reason)
        return 0

    include_executives = args.solo != "lideres"
    include_leaders = True if args.solo == "lideres" else (False if args.solo == "ejecutivos" else None)
    summary = asyncio.run(
        run_reminder(
            dry_run=args.dry_run,
            include_executives=include_executives,
            include_leaders=include_leaders,
        )
    )
    return 1 if summary.failed else 0


if __name__ == "__main__":
    sys.exit(main())
