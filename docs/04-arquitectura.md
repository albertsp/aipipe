# Arquitectura

Cómo está construido aipipe y por qué. Para usarlo no hace falta leerlo; sirve para modificarlo, depurarlo o decidir
si encaja con lo que necesitas.

## Principios de diseño

1. **Solo librería estándar de Python.** Sin dependencias que instalar, actualizar ni auditar. Linear se habla con
   `urllib` (GraphQL).
2. **Todo el acceso a modelos pasa por `opencode run`.** Es el cliente que Go valida. aipipe nunca llama a la API de Go.
3. **El LLM propone, las reglas deciden.** El triaje del modelo barato sugiere complejidad, riesgo y número de archivos;
   el tier final lo fijan reglas deterministas que se pueden leer y probar.
4. **Aislamiento con `git worktree`.** Cada ticket se trabaja en una copia aparte; tu checkout nunca se toca.
5. **Los agentes no hacen commit ni push.** Lo hace el orquestador, después de pasar los tests y la revisión.
6. **Mientras se espera, no hay procesos.** Un ticket que espera tu aprobación es solo un archivo JSON y una etiqueta.
7. **Fallar sin bucles.** Un error cambia la etiqueta de disparo por `ai-failed`; un ticket fallido no se relanza solo.
8. **Seguro por defecto con el texto de los tickets.** Solo se ejecutan tickets del dueño de la clave de Linear.

## Mapa de módulos

| Módulo | Responsabilidad |
|---|---|
| `cli.py` | Comandos: `init`, `install-agents`, `doctor`, `sandbox-check`, `route`, `budget`, `run`, `watch` y códigos de salida |
| `config.py` | Configuración en capas (defaults < global < proyecto) y raíz del proyecto |
| `pipeline.py` | `Runner`: el flujo de un ticket, el punto de control, la cancelación, la cola y los bloqueos |
| `router.py` | Elección de tier: etiquetas, triaje, reglas y heurística; parser tolerante del JSON de los modelos |
| `budget.py`, `ledger.py`, `pricing.py` | Libro de gasto, ventanas de Go, decisiones de pausa y bajada de tier, precios por modelo |
| `opencode.py` | Ejecución de `opencode run`, lectura de eventos y tokens, tiempo máximo, cancelación y detección de límites |
| `linear.py` | `LinearTracker`: consultas y mutaciones de Linear, filtro de creador, fases, espera, aprobación |
| `tickets.py` | Modelo de ticket, el protocolo `Tracker`, el tracker local de ficheros y el analizador de respuestas |
| `sandbox.py` | Entorno por lista blanca y sandbox (bubblewrap) para agentes y tests; autoprueba `sandbox-check` |
| `gitops.py` | Worktrees, ramas, commit, push y PR |
| `agents.py` | Genera e instala los seis agentes de OpenCode desde plantillas y la tabla de modelos |
| `paths.py` | Rutas de configuración, datos y estado |
| `templates/` | Plantillas de los agentes (`agents/*.md.tmpl`) y del `.aipipe.toml` |

## Flujo de un ticket

```
aipipe run / watch
   │
   ├─ waiting()  → tickets con ai-waiting: ¿hay respuesta? ──► aprobado ─► (reanuda abajo)
   │                                                        └► cambios  ─► rehace plan, vuelve a esperar
   │                                                        └► rechazado ─► Todo, sin ai-ready
   └─ ready()    → ai-ready + estado Todo + equipo + creador autorizado, por prioridad
        │
        ▼ por cada ticket
   bloqueos: ticket → repositorio → hueco global      (si ocupado: «queued», no se toca el ticket)
   presupuesto: ¿pausa? ──► sí: Todo + comentario
   start → In Progress ; worktree nuevo desde la rama base
        │
   etiqueta ai:*?  ── sí ─► tier directo (sin triaje)
        │ no
        ▼
   triaje (modelo barato) ─► JSON {complejidad, archivos, riesgo, plan}
        │
   router: complejidad 1→light · 2–3→standard · 4–5→heavy ; +1 tier si ≥ 8 archivos / riesgo alto / estimación ≥ 8
        │
   ¿plan? (tier ≠ light, o punto de control) ──► agente de plan
        │
   ¿punto de control? ──► comentario con el plan + ai-waiting ──► FIN (sin proceso). Se reanuda en la siguiente pasada
        │
        ▼ intentos 1..3 (a partir del 3.º sube un tier)
   presupuesto: ¿pausa / bajar de tier?
   agente de implementación ─► ¿hay cambios? ─► tests (test_command) ─► revisión (diff, otro modelo)
        │ fallo en cualquier paso: el motivo vuelve como feedback al siguiente intento
        ▼ éxito
   commit en la rama ai/<id>-<resumen> ─► [push ─► PR]
   comentario (tier, intentos, coste, rama) + In Review
        │ sin éxito tras 3 intentos
        └──► comentario + Todo + ai-failed
```

Mientras corre un agente, aipipe consulta cada `cancel_check_s` segundos (y antes de cada paso y alrededor de los tests)
si alguien sacó la issue de *In Progress*. Si es así, mata el agente y sus procesos hijos (grupo de procesos) y se
detiene sin tocar el estado que puso el usuario.

## Decisiones y mecanismos clave

### Router

Orden de decisión: **etiqueta explícita** (`ai:light`, `ai:std`, `ai:heavy`) → **triaje** + reglas → **heurística por
palabras clave** si el triaje no devuelve JSON válido. Las reglas del triaje: complejidad 1 = *light*, 2–3 = *standard*,
4–5 = *heavy*; sube un tier si el triaje estima 8 o más archivos, si marca riesgo alto o si la estimación del ticket en
Linear es 8 o más. Un ticket *light* nunca lleva plan (salvo punto de control).

El reintento se hace en el mismo tier; a partir del intento posterior a `escalate_after` sube un tier. El revisor solo
pide cambios por fallos reales, y si en el último intento queda con reservas, se anotan en el comentario del ticket.

### Presupuesto

Cada llamada anota en `ledger.jsonl` tokens, coste a precio de lista y su **fracción del cupo mensual del modelo**
(coste / límite mensual del modelo dentro de Go). Se suman esas fracciones por ventana y se comparan con los topes de
Go: 20 % para 5 horas, 50 % para la semana y 100 % para el mes. Así un modelo con límite de 15 $ gasta cupo cuatro veces
más rápido por dólar que uno de 60 $. Si la documentación de Go se interpretara como un pozo común con topes por modelo,
la suma sigue siendo un límite superior válido.

Limitaciones asumidas: solo cuenta lo que ejecuta aipipe, las ventanas se asumen móviles y los precios son de lista
(DeepSeek a mitad de precio fuera de las horas pico, 01–04 y 06–10 UTC de lunes a viernes). Si OpenCode informa de un
límite de uso, aipipe pausa 5 horas aunque su contador no lo prevea.

### Punto de control sin procesos

Al llegar al punto de control, aipipe guarda un JSON en `~/.local/state/aipipe/waiting/<ID>.json` (plan, tier, id y fecha
del comentario del plan, revisiones usadas, repositorio), añade `ai-waiting`, quita `ai-ready` y descarta el worktree.
La issue sigue en *In Progress*. En cada pasada, aipipe consulta los comentarios de los tickets con `ai-waiting` y solo
acepta una respuesta si es **posterior al comentario del plan, de un usuario autorizado y el comentario entero es una
respuesta válida**. Al aprobar se recrea el worktree desde la base actual y se retoma el flujo con el plan guardado.

Si se pausa por presupuesto después de aprobar, el ticket vuelve a `ai-waiting`: la aprobación sigue en Linear y
continuará solo cuando haya cupo.

### Cola y bloqueos

Tres bloqueos por archivo (`~/.local/state/aipipe/locks/`), en este orden: **ticket**, **repositorio** (nunca dos
ejecuciones en el mismo repo) y **hueco global** (`runner.max_concurrent` huecos en toda la máquina). Un bloqueo guarda el
PID de su dueño; si ese proceso ya no existe, el bloqueo se considera huérfano y se limpia. Lo que no cabe termina como
`queued` y el ticket se deja intacto.

### Entrega

El orquestador hace un único commit por ticket (`ALB-32: título`, con enlace al ticket). Con `push = true` y remoto, sube
la rama; con `pr = true`, abre el PR con `gh`. Si la entrega falla, el trabajo queda commiteado en la rama local y el
ticket se marca como fallido con el motivo.

### Agentes y permisos

| Agente | Rol | Permisos de OpenCode |
|---|---|---|
| `aipipe-triage` | Clasifica complejidad | Solo lectura, sin bash, sin web; 6 pasos |
| `aipipe-plan` | Plan breve (≤ 12 líneas) | Solo lectura, sin bash, sin web; 12 pasos |
| `aipipe-impl-light/std/heavy` | Implementa | Edita; bash permitido salvo una lista de denegados (`git push/commit/checkout/switch/reset/clean/rebase/merge`, `rm -rf`, `sudo`, `curl`, `wget`, `ssh`, `scp`); sin web ni subagentes; sin salir del directorio; 20 / 40 / 60 pasos según el tier (light / standard / heavy) |
| `aipipe-review` | Revisa el diff | Solo `git diff`, `git log` y `git show`; 8 pasos |

El prompt de implementación incluye el contenido del ticket entre etiquetas `<ticket>` y una regla que lo declara
especificación de trabajo (no instrucciones sobre el entorno) y pide ignorar peticiones de red, de leer credenciales o de
salir del repositorio. **Esa regla reduce el riesgo, no lo elimina.** Los límites de verdad son los permisos del
sistema operativo: ver [Seguridad](05-seguridad.md), donde también se explica por qué una lista de denegados no basta.

### Linear

Se valida contra el esquema GraphQL oficial en los tests opcionales (11 operaciones y todos los inputs). La clave va en
la cabecera `Authorization` (sin `Bearer`). Un fallo transitorio de red se reintenta tres veces con espera creciente; un
fallo al consultar el estado nunca se interpreta como cancelación. Las etiquetas de fase se cambian con **una sola
mutación** que solo quita las que la issue tiene ahora.

## Pruebas

79 pruebas siempre activas (`pytest`): unitarias (router, precios, presupuesto, analizador de respuestas) y de punta a
punta con un OpenCode simulado y un servidor de Linear simulado con estado. Opcionales:

- `AIPIPE_LINEAR_SCHEMA=schema.graphql pytest tests/test_linear_schema.py`: valida las operaciones contra el esquema oficial.
- `AIPIPE_REAL_OPENCODE=1 pytest tests/test_real_opencode.py`: usa OpenCode real con un proveedor simulado local.

Con OpenCode real aparecieron dos fallos graves que los simuladores no veían, ya corregidos: `opencode run` **se bloquea
si hereda un stdin abierto** (de ahí `stdin=DEVNULL`), y sin fijar `PWD` el agente **escribía en el checkout principal**
en lugar de en el worktree.

## Decisiones abiertas

- **Cómo dispara Linear al runner (ALB-9).** En la práctica aipipe usa **sondeo** (`watch`): la opción más simple y que
  no expone ningún puerto. Los *webhooks* y el modelo de agentes de Linear no se han evaluado todavía.
- **Cómo se ejecuta OpenCode sin interfaz (ALB-10).** Se usa `opencode run` por proceso. No se ha comparado con
  `opencode serve`. Como las peticiones a Go las hace OpenCode y no aipipe, el *user agent* y la cabecera
  `x-opencode-session` que pide la documentación de Go no los controla aipipe; falta comprobar qué envía OpenCode.
- **Filtro por proyecto de Linear.** Hoy solo se filtra por equipo (ver la [guía](01-guia-de-uso.md#11-varios-proyectos)).

## Cómo extenderlo

- **Cambiar un modelo:** edita `[models]` y ejecuta `aipipe install-agents --force`.
- **Añadir un modelo nuevo de Go:** si no está en `PRICES` de `pricing.py`, se trata como caro; añade su precio y límite
  (o sobrescríbelo en `[pricing]`).
- **Cambiar los permisos de un agente:** edita las plantillas de `templates/agents/` y reinstala con `--force`.
- **Otro tracker (GitHub Issues, Jira…):** implementa el protocolo `Tracker` de `tickets.py`. Los métodos básicos son
  `ready`, `get`, `start`, `comment`, `finish`, `fail` y `release`; los opcionales (cancelación, aprobación, fases)
  se detectan con `getattr`, así que un tracker sencillo funciona sin ellos.
- **Probar sin gastar:** `aipipe run --from-file ticket.md --dry-run` y `aipipe route`.
