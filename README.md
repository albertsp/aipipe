# aipipe

Pipeline **Linear → OpenCode (plan Go de 10 $)** para cualquier proyecto. Un orquestador en Python que toma
tickets de Linear, elige el modelo con un router, ejecuta agentes por rol en un `git worktree` aislado, verifica con los
tests del proyecto y lo cuenta todo en el ticket. Pensado para desarrollar de principio a fin **desde el móvil**: creas el
ticket, apruebas el plan con un comentario y revisas el resultado, mientras el trabajo corre en un servidor.

- Solo librería estándar de Python (≥ 3.11), sin dependencias.
- Toda llamada a modelos pasa por `opencode run` (el cliente que Go valida). aipipe nunca habla con la API de Go.
- Presupuesto bajo control: umbrales por ventana de Go y, sobre todo, «Use balance» desactivado en la consola de OpenCode.
- Solo ejecuta tickets **creados por ti**: el texto de una issue llega a un agente con shell.

```
Linear (etiqueta ai-ready, estado Todo)
  → triaje (modelo barato) → router por reglas → [plan → aprobación por comentario]
  → implementar (tier light/standard/heavy) → tests → revisión → commit
  → comentario + estado «In Review» en Linear → gasto anotado en el ledger local
```

## Estado del proyecto (5-oct-2026, versión 0.5.0)

| | |
|---|---|
| Validado en un VPS real (Ubuntu 26.04, OpenCode 1.18.34, Python 3.14) | Un ticket de punta a punta contra Linear y Go reales: triaje → implementación → tests → revisión → *In Review*, 1 intento, ≈ 5 min, ≈ 0,011 $ a precio de lista |
| Validado con tests (79 siempre activos + 17 opcionales de esquema de Linear) | Router, presupuesto, bloqueos, cola, cancelación, punto de control, filtro de creador |
| Validado en real: punto de control (5-oct-2026) | ALB-33 con `ai:approve`: plan comentado en Linear → `aprobado` desde el móvil → implementación en tier *standard* (`kimi-k2.7-code`), tests y revisión aprobados en 1 intento, ≈ 0,028 $ en total; el diff era correcto (con test del caso de división por cero) |
| Validado en real: cancelación (5-oct-2026) | Sacar la issue de *In Progress* mientras corre detiene la ejecución |
| **Aún sin validar en real** | Pausa por presupuesto, `push` + PR con `gh`, `watch` como servicio systemd, tier *heavy*, CodeGraph y Graft |
| Implementado, **sin validar aún en el VPS** (0.5.0) | Sandbox (bubblewrap) y entorno por lista blanca: los agentes y los tests no ven la clave de Linear ni las credenciales de GitHub (ALB-27). Probado con bubblewrap real en desarrollo; falta `aipipe sandbox-check` y un ticket real en el servidor |
| **Pendiente de seguridad** | Clave de Linear con alcance mínimo (ALB-28), regla anti-inyección en todos los prompts (ALB-29), lista de comandos permitidos (ALB-30) y salida de red limitada (ALB-31). Ver [seguridad](docs/05-seguridad.md) |

Usa aipipe de momento **solo sobre repositorios de prueba** hasta cerrar lo pendiente de seguridad.

## Empezar en cinco minutos (ya instalado)

```bash
cd mi-proyecto
aipipe init --team ENG --test-command "pytest -q"   # crea .aipipe.toml e instala los 6 agentes de OpenCode
aipipe doctor                                       # comprueba el entorno
aipipe route "Refactor del módulo de pagos"         # ensayo del router, sin gastar nada
aipipe run --from-file ticket.md                    # primera prueba real SIN Linear
aipipe run --issue ENG-12                           # un ticket concreto de Linear
aipipe watch --interval 300                         # vigila Linear y procesa la cola
```

En Linear: crea la etiqueta `ai-ready`, ponla en un ticket en estado **Todo** y aipipe lo recogerá. Si añades también
`ai:approve`, publica el plan como comentario y espera a que respondas `aprobado`.

## Documentación

| Documento | Para qué |
|---|---|
| [Guía de uso](docs/01-guia-de-uso.md) | El día a día: escribir tickets, etiquetas, aprobar, cancelar, revisar, presupuesto, trabajar desde el móvil |
| [Instalación en un VPS](docs/02-instalacion-vps.md) | De un servidor vacío a aipipe funcionando: SSH, cortafuegos, Tailscale, OpenCode, aipipe |
| [Configuración y comandos](docs/03-configuracion.md) | Todas las claves de `.aipipe.toml`, comandos, códigos de salida, rutas y variables |
| [Arquitectura](docs/04-arquitectura.md) | Cómo funciona por dentro, ciclo de vida de un ticket y decisiones de diseño |
| [Seguridad](docs/05-seguridad.md) | Modelo de amenazas, qué protege aipipe y qué no, y la lista de pendientes |
| [Operación](docs/06-operacion.md) | Runbook: arrancar, parar, servicio systemd, rotar claves, actualizar, copias |
| [Solución de problemas](docs/07-solucion-de-problemas.md) | Síntomas reales y su arreglo |

## Instalación resumida

```bash
python3 -m venv ~/venv && ~/venv/bin/pip install ./aipipe
echo 'export PATH=$HOME/venv/bin:$PATH' >> ~/.bashrc && source ~/.bashrc
aipipe install-agents          # agentes de OpenCode (globales)
```

Requisitos en el `PATH`: `opencode` (con `opencode auth login` → OpenCode Go hecho), `git` y, solo si quieres PRs, `gh`
autenticado. La clave de Linear va en la variable `LINEAR_API_KEY` (Linear → Settings → Account → Security & access →
Personal API keys). Guía completa, incluido el servidor: [instalación en un VPS](docs/02-instalacion-vps.md).

> **Imprescindible para no pasar de 10 $:** en la consola de OpenCode deja **desactivado** «Use balance». Es la única
> garantía dura. El control local de aipipe es una estimación a precio de lista.

## Comandos

| Comando | Qué hace |
|---|---|
| `aipipe init` | Crea `.aipipe.toml` e instala los agentes |
| `aipipe install-agents [--project] [--force]` | (Re)instala los agentes desde la tabla de modelos |
| `aipipe doctor` | Comprueba el entorno |
| `aipipe sandbox-check [--opencode]` | Prueba negativa: un agente dentro del sandbox no puede leer las claves |
| `aipipe route "texto" [--label ai:heavy]` | Ensayo del router, sin llamar a modelos |
| `aipipe budget [--add-usd 2.5 --note "..."]` | Uso estimado por ventana de Go |
| `aipipe run [--issue ID] [--from-file f.md] [--max N] [--dry-run]` | Una pasada sobre la cola |
| `aipipe watch [--interval 300]` | Vigila Linear en bucle |

Códigos de salida de `aipipe run`: `0` ok · `1` fallo · `2` error de configuración o de Linear · `3` pausa por límites ·
`4` en cola (otra ejecución ocupa el repositorio o el hueco global).

## Cómo decide el modelo

| Rol / tier | Modelo por defecto (`opencode-go/…`) | Cuándo |
|---|---|---|
| triaje | `mimo-v2.6-flash` | siempre, salvo etiqueta `ai:*` |
| plan | `minimax-m3` | tiers standard/heavy, o siempre que haya punto de control |
| light | `glm-5.3-flash` | tareas triviales (complejidad 1) |
| standard | `kimi-k2.7-code` | tareas normales (2–3) |
| heavy | `deepseek-v4-pro` | complejas (4–5), riesgo alto, ≥ 8 archivos o estimación ≥ 8 |
| revisión | `glm-5.3` | una pasada tras pasar los tests |

La asignación es **orientativa por precio y capacidad**: no se ha comparado la calidad de los modelos. Cámbiala en
`[models]` y ejecuta `aipipe install-agents --force`. Los seis modelos por defecto existen en la lista real de Go
(comprobado con `opencode models`).

## Desarrollo

```bash
pip install -e ".[dev]" && pytest
AIPIPE_LINEAR_SCHEMA=schema.graphql pytest   # opcional: valida las consultas contra el esquema oficial de Linear
AIPIPE_REAL_OPENCODE=1 pytest                # opcional: OpenCode real con un proveedor simulado
```

Más sobre el diseño y cómo extenderlo en [arquitectura](docs/04-arquitectura.md).
