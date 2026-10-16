# BOT_ACTIVITY_REMINDER

Recordatorio por Slack de las actividades de Salesforce:

- **A cada ejecutivo**: sus tareas **vencidas** y sus **tareas del día**.
- **A cada líder**: cuántas tareas vencidas (y del día) tiene cada persona a su cargo.

Sigue el mismo esquema que `BOT_ASSIG_LIDER`: cliente Salesforce por API
(login SOAP u OAuth), app de Slack con los mismos scopes y configuración
editable en JSON, sin tocar código.

## Criterios (Salesforce)

| Concepto | Regla |
|---|---|
| Actividad vencida | `Task` abierta (`IsClosed = false`) con `ActivityDate` **anterior** a hoy |
| Tarea del día | `Task` abierta con `ActivityDate` = hoy |
| "Hoy" | fecha en `America/Bogota` (`reminder_config.json → timezone`), no el `TODAY` de SOQL |
| Líder de un ejecutivo | su `Manager` en el usuario de Salesforce |
| Ejecutivos (producción) | usuarios activos con `Title` en `reminder_config.json → executive_titles` |

Los mensajes muestran **siempre el total** y listan las primeras N actividades
(`max_overdue_items`, `max_today_items`); el resto aparece como "…y N más"
con un enlace a Salesforce. Ejemplo real: un ejecutivo de prueba tiene 385
vencidas, y un mensaje de Slack no puede listarlas todas.

## Modo prueba (estado actual)

Con `TEST_MODE=true` en `.env`:

- Solo se procesan `Ejecutivotest1`, `Ejecutivotest2` y `Ejecutivotest3`.
- **Ningún mensaje llega al ejecutivo ni al líder real.** Cada mensaje se envía a:
  - el canal de pruebas (`SLACK_TEST_CHANNEL_ID`), y
  - por DM a `Ejecutivotestsend1`.
- Cada mensaje lleva un aviso `[PRUEBA]` que dice a quién le llegaría en producción.

Para pasar a producción: `TEST_MODE=false` y revisar `executive_titles`.

## Uso

```bash
# instalar (una vez)
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

.venv\Scripts\python main.py --check        # prueba conexión a Salesforce y Slack
.venv\Scripts\python main.py --dry-run      # muestra los mensajes en consola, NO envía
.venv\Scripts\python main.py                # envía el recordatorio ahora (una vez)
.venv\Scripts\python main.py --solo lideres # solo el resumen de líderes
.venv\Scripts\python main.py --forzar       # envía aunque hoy no sea día hábil
.venv\Scripts\python main.py --schedule     # queda corriendo y envía a las horas programadas
.venv\Scripts\python -m pytest -q           # pruebas
```

`--schedule` envía en las horas de `reminder_config.json → schedule.times`
(por defecto 08:00, solo días hábiles de Colombia). Alternativa: no usar
`--schedule` y programar `main.py` con el Programador de tareas de Windows:
en sábados, domingos y festivos no envía nada (salvo `--forzar`).

## Editar los mensajes — `message_templates.json`

Todos los textos de Slack están ahí. Se pueden cambiar libremente:

- Variables entre llaves: `{nombre}`, `{total_vencidas}`, etc. Cada sección
  lista en `_variables` cuáles puede usar. Si escribes una que no existe,
  el mensaje la muestra tal cual (`{asi}`) y el log avisa, pero no falla.
- Formato Slack: `*negrita*`, `_cursiva_`, `<url|texto>`, `:emoji:`.
- Llave literal: `{{` o `}}`.
- Formato de fechas: `formato_fecha`, `formato_fecha_hora`.

Después de editar, revisar con `python main.py --dry-run`.

## Otras opciones — `reminder_config.json`

| Clave | Para qué |
|---|---|
| `executive_titles` | Quiénes son ejecutivos en producción |
| `max_overdue_items` / `max_today_items` | Cuántas actividades listar por sección |
| `overdue_order` | `newest_first` (las vencidas más recientes primero) u `oldest_first` |
| `send_when_empty` | Enviar o no el mensaje a quien no tiene pendientes |
| `leaders.enabled` | Enviar el resumen a líderes (hoy `false`: la prueba es solo de ejecutivos) |
| `schedule` | Horas de envío, solo días hábiles, margen de catch-up |

## Estructura

```
main.py                     CLI (--check, --dry-run, --schedule, --solo)
config.py                   .env + carga de los JSON
reminder_config.json        horario, límites, criterios
message_templates.json      textos de Slack
services/salesforce_client  login + consultas de User y Task (solo lectura)
services/slack_service      envío (canal / DM)
services/reminder           extracción → armado → envío según modo prueba
services/scheduler          envío programado
utils/message_builder       arma los blocks de Slack desde los templates
utils/business_days         días hábiles de Colombia
```
