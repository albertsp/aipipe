# Configuración y comandos

Referencia completa. Para el uso diario, ver la [guía de uso](01-guia-de-uso.md).

## Cómo se combina la configuración

Tres capas; cada una pisa a la anterior. Las tablas se fusionan clave a clave, y una lista **sustituye** a la anterior
completa.

1. **Valores por defecto** (en el código, `config.py`).
2. **Global**: `~/.config/aipipe/config.toml`. Lo común a todos tus proyectos (modelos, presupuesto).
3. **Proyecto**: `.aipipe.toml` en la raíz del repositorio (se commitea). Lo propio de ese proyecto.

La raíz del proyecto es la primera carpeta, subiendo desde donde ejecutas, que tenga `.aipipe.toml` o `.git`.
`aipipe doctor` muestra qué archivos de configuración se están usando.

Ejemplo mínimo de `.aipipe.toml`:

```toml
[linear]
team = "ALB"

[project]
base_branch = "main"
test_command = "pytest -q"
push = false
pr = false
```

## `[linear]`

| Clave | Por defecto | Qué hace |
|---|---|---|
| `team` | `""` | Clave del equipo de Linear (por ejemplo `ALB`). Solo se miran issues de ese equipo. Vacío = todos los equipos (no recomendado) |
| `trigger_label` | `"ai-ready"` | Etiqueta que dispara a aipipe |
| `trigger_states` | `["Todo"]` | Estados en los que se recoge un ticket. El **primero** es también al que vuelven los tickets que fallan, se pausan o se rechazan |
| `state_in_progress` | `"In Progress"` | Estado mientras trabaja. Sacar la issue de este estado = parar |
| `state_in_review` | `"In Review"` | Estado al terminar con éxito |
| `failed_label` | `"ai-failed"` | Etiqueta que se pone si el ticket falla |
| `max_per_batch` | `5` | Tickets que se ejecutan como máximo por pasada |
| `allowed_creators` | `[]` | Emails o ids de usuario de Linear cuyos tickets se ejecutan. **Sustituye** al dueño de la clave |
| `allow_any_creator` | `false` | `true` desactiva el filtro de creador. **No recomendado** |
| `phase_labels` | `true` | Reflejar la fase en la issue con etiquetas (`ai-phase-*`) |
| `phase_prefix` | `"ai-phase-"` | Prefijo de esas etiquetas (`plan`, `impl`, `review`) |
| `waiting_label` | `"ai-waiting"` | Etiqueta de «esperando tu aprobación» |
| `approve_label` | `"ai:approve"` | Etiqueta que pide aprobación del plan (si `after_plan = "label"`) |
| `api_url` | `https://api.linear.app/graphql` | Endpoint (útil para pruebas) |
| `api_key_env` | `"LINEAR_API_KEY"` | Nombre de la variable de entorno con la clave |

Los nombres de estado y etiqueta se comparan sin distinguir mayúsculas. Si un estado no existe en el equipo, aipipe
falla con un error que lista los disponibles.

## `[checkpoints]`

| Clave | Por defecto | Qué hace |
|---|---|---|
| `after_plan` | `"label"` | `label`: solo los tickets con `approve_label`; `always`: todos; `never`: ninguno |
| `max_plan_revisions` | `2` | Veces que puedes responder `cambios: …` antes de tener que aprobar o rechazar |

Los puntos de control solo existen con Linear. Con `--from-file` no se aplican.

## `[models]`

Rol → modelo, en formato de OpenCode (`proveedor/modelo`). **Los agentes de OpenCode se generan a partir de esta
tabla:** después de cambiarla ejecuta `aipipe install-agents --force`. `aipipe doctor` avisa si falta alguno.

| Rol | Por defecto |
|---|---|
| `triage` | `opencode-go/mimo-v2.6-flash` |
| `plan` | `opencode-go/minimax-m3` |
| `light` | `opencode-go/glm-5.3-flash` |
| `standard` | `opencode-go/kimi-k2.7-code` |
| `heavy` | `opencode-go/deepseek-v4-pro` |
| `reviewer` | `opencode-go/glm-5.3` |

Para ver los modelos que ofrece tu plan: `opencode models | grep opencode-go`. Los modelos Muse Spark «Contributor»
entrenan con tus prompts y `doctor` avisa si los configuras.

## `[project]`

| Clave | Por defecto | Qué hace |
|---|---|---|
| `base_branch` | `"main"` | Rama de la que parten las ramas de trabajo |
| `branch_prefix` | `"ai/"` | Prefijo de la rama de cada ticket (`ai/alb-32-resumen`) |
| `test_command` | `""` | Comando de tests, ejecutado con shell **dentro del worktree**. Vacío = no se verifica (no recomendado) |
| `test_timeout_s` | `900` | Tiempo máximo de los tests |
| `worktrees_dir` | `""` | Dónde crear los worktrees. Vacío = `~/.local/share/aipipe/worktrees/<repo>` |
| `fetch` | `true` | Hacer `git fetch origin <base>` antes de crear el worktree (si hay remoto) |
| `push` | `true` | Subir la rama a `origin` al terminar. **`aipipe init` genera `true`**: ponlo en `false` hasta tener el remoto listo |
| `pr` | `true` | Abrir un PR con `gh pr create` (requiere `push` y `gh` autenticado). Ídem: ponlo en `false` al empezar |
| `max_attempts` | `3` | Intentos por ticket |
| `escalate_after` | `2` | A partir del intento posterior a este número, sube un tier |
| `use_review` | `true` | Pasada de revisión tras pasar los tests |
| `keep_worktree_on_fail` | `true` | Conserva el worktree de los tickets fallidos o cancelados para inspeccionarlo |
| `diff_chars_for_review` | `24000` | Tamaño máximo del diff que se envía al revisor |

Notas: con `push = true` pero sin remoto configurado, el trabajo queda en una rama local. Si hay remoto y la entrega falla,
el ticket se marca como fallido y el trabajo sigue commiteado en la rama local.

## `[budget]`

Fracciones del cupo de Go. Ventanas de Go: 5 h = 20 %, semana = 50 %, mes = 100 % del cupo mensual de cada modelo.

| Clave | Por defecto | Qué hace |
|---|---|---|
| `heavy_stop` | `0.50` | Por encima de esta fracción de cualquier ventana, *heavy* baja a *standard* |
| `standard_stop` | `0.70` | Por encima, *standard* baja a *light* |
| `soft_stop` | `0.85` | Por encima, **se pausa la cola** y el ticket vuelve a *Todo* |

## `[runner]`

| Clave | Por defecto | Qué hace |
|---|---|---|
| `max_concurrent` | `1` | Ejecuciones simultáneas en **toda la máquina**. Además, nunca hay dos en el mismo repositorio |
| `cancel_check_s` | `20` | Cada cuántos segundos se mira en Linear si sacaste la issue de *In Progress* |

## `[opencode]`

| Clave | Por defecto | Qué hace |
|---|---|---|
| `bin` | `"opencode"` | Ejecutable de OpenCode |
| `extra_args` | `[]` | Argumentos extra para `opencode run` |
| `timeout_s` | `triage 300, plan 600, light 900, standard 1800, heavy 2400, review 600` | Tiempo máximo por rol, en segundos |

aipipe lanza siempre `opencode run --agent <agente> --format json --auto --dir <worktree> --title "<ticket> <agente>"`.
Añade `OPENCODE_DISABLE_AUTOUPDATE=1` al entorno (si no lo definiste tú) para que OpenCode no se actualice a mitad de una
ejecución: actualízalo a mano ([Operación](06-operacion.md)).

## `[sandbox]`

Aislamiento de lo que ejecuta código no confiable: **los agentes de OpenCode y los tests del proyecto** (ALB-27). Ver
[seguridad](05-seguridad.md) para el porqué y los límites.

| Clave | Por defecto | Qué hace |
|---|---|---|
| `mode` | `"auto"` | `"bwrap"`: obligatorio (si no está disponible, `run` y `watch` terminan con error, sin degradar en silencio). `"auto"`: usa bubblewrap si funciona y, si no, ejecuta **sin aislamiento** con un aviso. `"off"`: sin aislamiento. **En el VPS, usa `"bwrap"`** |
| `bwrap_bin` | `"bwrap"` | Ejecutable de bubblewrap |
| `env_allow` | `PATH, HOME, USER, LOGNAME, SHELL, LANG, LANGUAGE, LC_*, TERM, TZ, TMPDIR, PWD, OPENCODE_*, XDG_*, NO_COLOR, CI, *_PROXY, SSL_CERT_*, NODE_EXTRA_CA_CERTS, REQUESTS_CA_BUNDLE, PYTHON*, VIRTUAL_ENV, NODE_ENV, NODE_OPTIONS` | Variables de entorno que **sí** llegan al agente y a los tests (patrones `fnmatch`). Las demás no |
| `env_allow_extra` | `[]` | Para añadir variables sin repetir la lista anterior |
| `ro_paths` | `[".opencode", ".config/opencode/agents", ".config/opencode/opencode.json", ".config/opencode/opencode.jsonc", "venv"]` | Rutas (relativas al HOME o absolutas) visibles en **solo lectura** dentro del sandbox. `~/.config/opencode` no se monta entero porque OpenCode escribe ahí al arrancar (probado: con la carpeta en solo lectura falla); solo se montan sus agentes y su configuración, y lo demás es efímero |
| `rw_paths` | `[".local/share/opencode", ".local/state/opencode", ".cache/opencode"]` | Rutas visibles y **escribibles** (datos y caché de OpenCode, incluida su clave de Go) |
| `extra_ro`, `extra_rw` | `[]` | Para añadir rutas sin repetir las listas anteriores (por ejemplo, un Node instalado en tu HOME) |
| `tests_network` | `true` | `false`: los tests corren sin red |

Qué ve un agente dentro del sandbox:

- **HOME vacío y efímero** (lo que se escriba fuera de las rutas `rw_paths` y del worktree desaparece al terminar) salvo
  lo listado en `ro_paths`/`rw_paths` y el worktree del ticket. No existen `~/.config/aipipe` (clave
  de Linear), `~/.config/gh` ni `~/.ssh`.
- **`/proc` propio:** los procesos del runner no existen; `/proc/$PPID/environ` no revela nada.
- **El resto del sistema, en solo lectura;** `/tmp` privado; sin `sudo` ni capacidades.
- **El metadato de git** (`.git` del worktree y del repositorio principal) en **solo lectura**: puede hacer `git status` y
  `git diff`, pero no dejar hooks que el runner ejecutaría después.
- **Nunca** llegan al hijo `LINEAR_API_KEY` (ni el nombre que pongas en `api_key_env`), `GH_TOKEN` ni `GITHUB_TOKEN`,
  aunque los añadas a `env_allow_extra`.

Las variables que apunten a archivos (`SSL_CERT_FILE`, `NODE_EXTRA_CA_CERTS`…) solo sirven si el archivo es visible dentro
del sandbox: si está en tu HOME, añádelo a `extra_ro`.

La variable `AIPIPE_SANDBOX=off|auto|bwrap` tiene prioridad sobre `mode` (útil para depurar y para los tests).

## `[pricing]`

Sobrescribe los precios de la tabla interna (`pricing.py`, de la documentación de Go del 3-oct-2026), en dólares por
millón de tokens. Útil si Go cambia precios o añade un modelo.

```toml
[pricing."kimi-k2.7-code"]
input = 0.95
output = 4.00
cache_read = 0.19
cache_write = 0       # 0 = sin precio propio: se factura como input
limit = 60            # límite mensual del modelo dentro de Go, en $
```

Un modelo desconocido se trata como caro (`3,00 / 15,00` y límite de 15 $) para no subestimar el gasto.

## Comandos

### `aipipe init`

Crea `.aipipe.toml` e instala los agentes. Opciones: `--team ENG`, `--test-command "pytest -q"`, `--base main`
(si no la indicas, la deduce del remoto `origin` o de la rama actual), `--no-agents`, `--force` (regenera). Falla si no estás en un repositorio git.

### `aipipe install-agents`

Instala o actualiza los seis agentes de OpenCode desde la tabla `[models]`. Por defecto en
`~/.config/opencode/agents/` (válidos para todos tus proyectos); con `--project`, en `.opencode/agents/` del repositorio.
Sin `--force` no sobrescribe uno que difiere.

### `aipipe doctor`

Comprueba el entorno: Python, OpenCode, git, `gh` (solo si `pr = true`), repositorio, configuración, equipo, clave de
Linear, política de creadores, **estado del sandbox**, modo del punto de control, comando de tests, modelos, agentes, CodeGraph y Graft. Devuelve
código `1` si hay algún `ERR`. Debe ejecutarse **dentro del repositorio** y con la clave cargada.

### `aipipe sandbox-check [--opencode]`

Prueba negativa: ejecuta **dentro del sandbox** lo que haría un agente manipulado (`env`, `/proc/$PPID/environ`,
`/proc/*/environ`, leer `~/.config/aipipe/env`, `~/.config/gh`, `~/.ssh`, ver el proceso del runner) y comprueba que no
obtiene nada. Usa tu clave real si está cargada. Con `--opencode` comprueba además que OpenCode arranca dentro del
sandbox. Devuelve `1` si algo falla (y entonces no uses aipipe con repositorios reales). Ejecútalo tras instalar y tras
cada cambio en `[sandbox]`.

### `aipipe route`

Ensayo del router, sin llamar a ningún modelo ni a Linear.

```bash
aipipe route "Refactor del módulo de pagos"
aipipe route --file ticket.md --label ai:heavy
```

Sin triaje real, usa la heurística por palabras clave. En una ejecución real, el triaje del modelo puede ajustar el resultado.

### `aipipe budget`

Muestra el cupo usado y el gasto a precio de lista por ventana (5 h, semana, mes). `--add-usd 2.5 --note "pruebas"` anota
uso hecho fuera de aipipe.

### `aipipe run`

Una pasada sobre la cola.

| Opción | Efecto |
|---|---|
| `--issue ALB-32` | Un ticket concreto (repetible). Salta la comprobación de etiqueta y estado, no la de creador |
| `--from-file ticket.md` | Ticket local en markdown (`# Título` y cuerpo), sin Linear |
| `--max N` | Máximo de tickets a ejecutar |
| `--dry-run` | Muestra lo que haría, sin llamar a modelos ni cambiar nada |

### `aipipe watch`

Bucle que ejecuta `run` cada `--interval` segundos (por defecto 300). Si la cola se pausa por presupuesto, espera hasta la
hora estimada de reanudación. Los errores de Linear se muestran y se reintenta. Se detiene con `Ctrl+C`.

### Códigos de salida de `aipipe run`

| Código | Significa |
|---|---|
| `0` | Todo bien (también si no había nada que hacer, o un ticket quedó esperando aprobación) |
| `1` | Algún ticket falló |
| `2` | Error de configuración, no es un repositorio git, o error de Linear |
| `3` | Cola pausada por límites de presupuesto o de Go |
| `4` | Alguno quedó **en cola**: otra ejecución ocupa el repositorio o el hueco global. No es un fallo |

## Dónde guarda cosas aipipe

| Qué | Dónde |
|---|---|
| Configuración global | `~/.config/aipipe/config.toml` |
| Clave de Linear (la creas tú) | `~/.config/aipipe/env` (permisos `600`) |
| Agentes de OpenCode | `~/.config/opencode/agents/aipipe-*.md` |
| Libro de gasto | `~/.local/share/aipipe/ledger.jsonl` |
| Worktrees | `~/.local/share/aipipe/worktrees/<repo>/<id>/` |
| Estado de tickets que esperan aprobación | `~/.local/state/aipipe/waiting/<ID>.json` |
| Bloqueos (ticket, repositorio, hueco global) | `~/.local/state/aipipe/locks/` |
| Clave de Go | `~/.local/share/opencode/auth.json` (la gestiona OpenCode) |

Los bloqueos huérfanos (de un proceso que ya no existe) se limpian solos.

## Variables de entorno

| Variable | Para qué |
|---|---|
| `LINEAR_API_KEY` (o la de `api_key_env`) | Clave de Linear |
| `AIPIPE_HOME` | Reúne config, datos y estado en una sola carpeta (los tests la usan) |
| `AIPIPE_OPENCODE_HOME` | Carpeta de OpenCode donde buscar/instalar `agents/` |
| `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME` | Se respetan para las rutas de arriba |
| `OPENCODE_DISABLE_AUTOUPDATE` | aipipe la pone a `1` si no está definida |
| `AIPIPE_SANDBOX` | `off`, `auto` o `bwrap`: sustituye a `sandbox.mode` |
| `AIPIPE_LINEAR_SCHEMA`, `AIPIPE_REAL_OPENCODE` | Solo para pruebas opcionales (ver el README) |
