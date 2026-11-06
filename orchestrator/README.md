# Orquestador DAG

Grafo genérico de tareas para Odysseus. No está atado a Kenaly, Cinema ni a
ningún proyecto: el cliente manda tareas, dependencias y el endpoint.

## API

- `POST /api/orchestrator/attachments` — sube texto `{name, content}` y
  devuelve `{id, name, bytes}` (10 por tarea, 200 KB por archivo).
- `POST /api/orchestrator/run` — crea y arranca un run.
- `GET /api/orchestrator/runs` — lista runs del usuario.
- `GET /api/orchestrator/runs/{id}` — estado completo.
- `GET /api/orchestrator/runs/{id}/events` — SSE con snapshots.
- `POST /api/orchestrator/runs/{id}/cancel` — cancela.

## Ejemplo

```json
{
  "name": "informe",
  "model": "deepseek-flash",
  "endpoint_id": "ca231715",
  "max_parallel": 2,
  "tasks": [
    {
      "id": "investiga",
      "prompt": "Investiga el tema y devuelve 8 hechos con fuentes."
    },
    {
      "id": "borrador",
      "prompt": "Escribe un borrador de 600 palabras con esos hechos.",
      "depends_on": ["investiga"]
    },
    {
      "id": "critica",
      "prompt": "Señala huecos, exageraciones y afirmaciones sin fuente.",
      "depends_on": ["borrador"]
    },
    {
      "id": "final",
      "prompt": "Reescribe el borrador corrigiendo la crítica.",
      "depends_on": ["borrador", "critica"]
    }
  ]
}
```

```bash
curl -sS -X POST http://127.0.0.1:7000/api/orchestrator/run \
  -H 'Content-Type: application/json' \
  --data @dag.json
```

## UI

`/orchestrator` es un editor visual: nodos arrastrables, puertos para crear
dependencias, inspector por tarea, adjuntos, auto-layout por capas y textarea
JSON sincronizada. Los runs se siguen por SSE.

## Semántica

- Valida ids únicos, dependencias existentes y ciclos (Kahn) antes de arrancar.
- Los adjuntos se guardan como blobs 0600; el ejecutor inyecta su contenido
  como datos de referencia, nunca como instrucciones del sistema.
- Ejecuta por capas topológicas con `max_parallel` (1–8).
- Cada tarea recibe la salida de sus dependencias como contexto.
- `retries` por tarea; `timeout` por intento; `max_tokens` por tarea.
- Si una dependencia falla, sus consumidoras quedan `skipped`.
- Persiste cada cambio en `data/orchestrator/runs/<id>.json` (sin migración DB).
- El estado final es `done`, `error` o `cancelled`.

## Límites actuales

- Sin presupuesto en USD; el control es `max_tokens`, `timeout` y `max_parallel`.
- No hay merge automático: el merge es una tarea más que depende de varias.
- Los adjuntos sólo son texto UTF-8; binarios (PDF/imagen) no se extraen aquí.
