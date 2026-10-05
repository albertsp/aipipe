# Guía de uso

Esta guía explica el día a día con aipipe: cómo escribir tickets que un agente pueda ejecutar, cómo lanzarlos, aprobar
planes, cancelar, revisar el resultado y vigilar el gasto. Si todavía no lo tienes instalado, empieza por
[Instalación en un VPS](02-instalacion-vps.md).

> **Estado:** el flujo de un ticket, con y sin punto de control (aprobación del plan desde el móvil), está validado con Linear y Go reales (ver el ejemplo del final). La cancelación a mitad de ejecución también está validada en real. La pausa por presupuesto
> está probada con tests y servidores simulados, pero **todavía no con Linear real**. Donde importa, esta guía lo marca.

## 1. Cómo encaja todo

| Pieza | Qué hace |
|---|---|
| **Tú (móvil o web)** | Escribes el ticket, pones las etiquetas, apruebas o cancelas con un comentario o cambiando el estado |
| **Linear** | Es el panel de control: lo que ves en la issue es lo que está pasando |
| **aipipe** | Vigila Linear, decide el modelo, lanza los agentes, ejecuta los tests y escribe el resultado en la issue |
| **OpenCode (plan Go)** | Ejecuta los modelos. aipipe lo lanza con `opencode run` |
| **El servidor (VPS)** | Donde corre todo. Solo es accesible por Tailscale |

Una cosa importante: **aipipe no es un servicio mágico que reacciona solo.** Alguien tiene que estar ejecutando
`aipipe run` (una pasada) o `aipipe watch` (en bucle). Para trabajar de verdad desde el móvil necesitas `watch`
corriendo en el servidor (ver [Operación](06-operacion.md)).

## 2. El ciclo de un ticket

1. **Creas la issue** en Linear (estado *Todo*) con objetivo, criterios y restricciones (apartado 3).
2. **La marcas** con la etiqueta `ai-ready`. Opcionalmente `ai:approve` si quieres aprobar el plan antes de que escriba código.
3. **aipipe la recoge** en el siguiente sondeo (por defecto, cada 5 minutos en `watch`), la pasa a *In Progress* y trabaja.
4. **Si pediste aprobación**, te deja el plan como comentario y se detiene. Respondes `aprobado` desde donde estés.
5. **Implementa, prueba y revisa.** Si los tests fallan o la revisión pide cambios, reintenta (hasta 3 intentos, subiendo
   de modelo a partir del segundo reintento).
6. **Te avisa en la issue**: comentario con el resumen y la rama, y la issue pasa a *In Review*. Tú revisas y haces el merge.

Si algo falla, la issue vuelve a *Todo* con la etiqueta `ai-failed` y un comentario con el motivo. Nunca se queda en
bucle.

## 3. Escribir un buen ticket

El agente solo sabe lo que pone en el ticket y lo que encuentra en el repositorio. Cuanto más concreto, menos intentos
(y menos cupo de Go) gastará. Usa esta plantilla:

```markdown
## Objetivo
Una o dos frases: qué debe existir o cambiar cuando termine.

## Repositorio
Nombre o ruta del proyecto en el servidor.

## Criterios de aceptación
- [ ] Algo comprobable: "`divide(1, 0)` lanza `ValueError`", no "que funcione bien".
- [ ] Los tests nuevos cubren el caso anterior.
- [ ] `pytest -q` pasa.

## Restricciones
- No tocar otros archivos que X e Y.
- No instalar dependencias nuevas.
```

Reglas prácticas:

- **Un ticket, un cambio.** Si necesitas «y además…», son dos tickets. Los tickets pequeños se resuelven en el tier
  barato y con un intento.
- **Criterios que se puedan comprobar con los tests.** aipipe da por buena una solución cuando pasa `test_command` y la
  revisión. Si no hay un test que la distinga, no hay forma de saber que está bien.
- **Pon restricciones.** «No tocar otros archivos» evita cambios colaterales y hace más fácil la revisión.
- **No pegues secretos ni datos reales** en el ticket: lo lee un modelo y queda escrito en Linear.
- **Texto copiado de la web:** pónle `ai:approve`. Puede contener instrucciones ajenas (ver [Seguridad](05-seguridad.md)).
- Solo se ejecutan tickets **creados por ti** (el dueño de la API key). Si otra persona crea el ticket, aipipe lo
  descarta con un aviso.

## 4. Etiquetas y estados

### Etiquetas que pones tú

| Etiqueta | Efecto |
|---|---|
| `ai-ready` | Dispara a aipipe: el ticket está listo (solo se recoge si está en *Todo*) |
| `ai:approve` | Pide aprobación del plan antes de implementar |
| `ai:light`, `ai:std`, `ai:heavy` | Fuerzan el tier y se salta el triaje (ahorra una petición). Con `ai:std` y `ai:heavy` hay plan previo |

Las etiquetas `ai-ready` y `ai:approve` hay que crearlas una vez en el equipo de Linear. Las de fase y fallo las crea
aipipe solas.

### Lo que verás en la issue mientras trabaja

| Fase | Estado | Etiqueta |
|---|---|---|
| En cola | Todo | `ai-ready` |
| Triaje y plan | In Progress | `ai-phase-plan` |
| Esperando tu aprobación | In Progress | `ai-waiting` |
| Implementando y tests | In Progress | `ai-phase-impl` |
| Revisión | In Progress | `ai-phase-review` |
| Terminado | In Review | ninguna (se limpian todas) |
| Falló | Todo | `ai-failed` (se quita `ai-ready`) |
| Pausa por cupo de Go | Todo (con `ai-ready`) | vuelve a la cola y sigue cuando haya cupo |
| Cancelado por ti | el que pusiste | se quita `ai-ready` |
| Plan rechazado | Todo | sin `ai-ready` |

Los nombres de estado (`In Progress`, `In Review`) deben coincidir con los de tu equipo. Se cambian en `[linear]`
(ver [Configuración](03-configuracion.md)).

## 5. Lanzar el trabajo

| Quieres | Comando |
|---|---|
| Ver qué haría sin gastar nada | `aipipe run --dry-run` |
| Procesar la cola una vez | `aipipe run` |
| Procesar un ticket concreto (aunque no tenga `ai-ready`) | `aipipe run --issue ALB-32` |
| Probar con un ticket local, sin Linear | `aipipe run --from-file ticket.md` |
| Dejarlo vigilando | `aipipe watch --interval 300` |

Detalles que conviene saber:

- Ejecutar desde la carpeta del repositorio. aipipe busca `.aipipe.toml` o `.git` hacia arriba.
- `run` sin `--issue` mira primero los tickets que esperan aprobación (no cuesta nada) y después los nuevos, por
  prioridad de Linear (urgente primero; sin prioridad, al final).
- `max_per_batch` (por defecto 5) limita cuántos tickets se ejecutan por pasada.
- `--issue` salta la comprobación de etiqueta y de estado, pero **no** la del creador, ni el punto de control si el
  ticket tiene `ai:approve`.
- Con `--from-file` no hay Linear: no hay punto de control ni etiquetas, y los comentarios salen por pantalla. Es la
  forma más barata de probar la instalación.

## 6. Aprobar un plan

Cuando un ticket tiene `ai:approve` (o `[checkpoints] after_plan = "always"`), aipipe hace triaje y plan, **publica el
plan como comentario y se detiene**. Mientras espera no hay ningún proceso ni se gastan tokens.

Respondes con **un comentario que contenga solo** una de estas cosas:

| Respuesta | Resultado |
|---|---|
| `aprobado` (también `ok`, `vale`, `adelante`, `lgtm`, `dale`, 👍, ✅) | Implementa el plan |
| `cambios: <lo que quieres distinto>` | Rehace el plan y vuelve a esperar (máximo 2 veces, `max_plan_revisions`) |
| `rechazado` (también `cancelar`, `stop`) | Devuelve la issue a *Todo* sin implementar y sin `ai-ready` |

Qué **no** cuenta como respuesta (a propósito, porque aprobar equivale a mandar ejecutar código):

- Una frase suelta: «ok, luego lo miro», «¿aprobado?», «aprobado pero cambia X».
- Un comentario anterior al plan.
- Un comentario de otra persona.
- El propio texto del plan, que contiene esas palabras.

Para que aipipe vea tu respuesta, `aipipe run` o `aipipe watch` tienen que ejecutarse después de comentar. Con `watch`
en marcha tarda como mucho un intervalo de sondeo.

> **Cuándo usar `ai:approve`:** tickets que tocan autenticación, despliegue, datos o dependencias; tickets con texto
> copiado de fuentes externas; cambios grandes (tier heavy) y las primeras veces que uses aipipe en un proyecto.
> Para tareas triviales no merece la pena: el plan es una espera más.

## 7. Cancelar, reintentar y limpiar

**Parar un trabajo en marcha:** saca la issue de *In Progress* (a *Backlog*, *Canceled*, *Todo*, lo que sea). aipipe lo
detecta (cada 20 s mientras corre un agente, y antes de cada paso), mata al agente y a los procesos que lanzó, deja un
comentario, **quita `ai-ready`** para que no se relance sola y **no toca el estado** que pusiste. El trabajo parcial
queda en su rama. El gasto hasta el corte se anota igualmente. Un fallo de la API de Linear nunca cuenta como cancelación.

**Reintentar un ticket fallido:** lee el comentario, corrige el ticket si hace falta, quita `ai-failed` y vuelve a
poner `ai-ready`.

**Un ticket que esperaba aprobación y sacaste de *In Progress*:** no hay proceso que parar, pero queda un archivo de
estado local. Bórralo: `rm ~/.local/state/aipipe/waiting/<ID>.json`.

**Ticket en cola:** si ves en la salida `queued`, otra ejecución ocupa el repositorio o el hueco global
(`runner.max_concurrent`, por defecto 1). El ticket no se toca y se reintenta en el siguiente sondeo.

## 8. Revisar y entregar el resultado

Con `push = false` (lo recomendado al principio), el trabajo queda en una **rama local** del repositorio del servidor,
con el nombre `ai/<id>-<resumen>` y un único commit (`ALB-32: título del ticket`):

```bash
cd ~/work/mi-proyecto
git branch                                        # ramas ai/…
git diff main..ai/alb-32-…                        # qué cambió
git log main..ai/alb-32-… --stat
git merge ai/alb-32-…                             # si estás conforme
```

Con `push = true` se sube la rama a `origin`, y con `pr = true` se abre además un PR con `gh pr create`. Eso exige
tener `gh` autenticado en el servidor y está **sin validar en real** todavía.

El comentario que deja aipipe en la issue incluye: tier y modelo final, número de intentos, coste estimado a precio de
lista, rama (o PR) y, si el revisor dejó reservas que no se resolvieron en el último intento, las menciona.

## 9. Presupuesto y monitorización

```bash
aipipe budget
```

Muestra el cupo usado por ventana de Go (5 h, semana, mes) y el gasto a precio de lista. Umbrales por defecto:

| Fracción del cupo | Qué hace aipipe |
|---|---|
| ≥ 50 % de cualquier ventana | Deja de usar *heavy* (baja a *standard*) |
| ≥ 70 % | Deja de usar *standard* (baja a *light*) |
| ≥ 85 % | **Pausa la cola**: devuelve el ticket a *Todo* con un comentario y reanuda cuando caduquen las entradas más antiguas |

Y recuerda tres cosas:

1. **Solo cuenta lo que ejecuta aipipe.** Si usas OpenCode a mano, anótalo: `aipipe budget --add-usd 2.5 --note "pruebas"`.
2. **Es una estimación.** El coste sale de tokens × precios de lista (`pricing.py`, de la documentación de Go del 3-oct-2026).
   La fuente autoritativa es la consola de Go. Los límites de Go pueden cambiar.
3. **«Use balance» desactivado** en la consola de OpenCode es la única garantía dura de no pasar de 10 $.

Registro detallado: `~/.local/share/aipipe/ledger.jsonl` (una línea por llamada: ticket, agente, modelo, tokens, coste).
También sirve `opencode stats --days 1 --models`.

## 10. Trabajar solo desde el móvil

Requisitos, una vez: servidor con `aipipe watch` corriendo como servicio ([Operación](06-operacion.md)), la app de
Tailscale en el móvil (para llegar al servidor si hace falta) y la app de Linear.

El flujo diario:

1. En la app de Linear, **crea la issue** con la plantilla y ponle `ai-ready` (y `ai:approve` si quieres aprobar el plan).
2. **Espera el comentario del plan** (si pediste aprobación) y respóndele `aprobado`.
3. **Cuando pase a *In Review***, lee el resumen. Si estás conforme, haz el merge (desde el móvil, abriendo el PR si
   usas `push`/`pr`; o entrando al servidor por SSH si el trabajo está en una rama local).
4. **Para parar algo**, cambia el estado de la issue.

Qué no está resuelto todavía:

- **Avisos al móvil:** no hay un canal propio. Linear notifica los comentarios nuevos en tus issues; no está probado si
  basta (issue ALB-17).
- **Merge desde el móvil sin SSH:** requiere `push = true` y `pr = true`, que aún no está validado.
- **Estado del servidor** (cola, errores recientes): hoy solo por SSH. El panel está pendiente (ALB-20).

## 11. Varios proyectos

Cada proyecto lleva su propio `.aipipe.toml`, que se commitea. Lo común a todos (modelos, presupuesto) puede ir en
`~/.config/aipipe/config.toml`.

**Limitación importante:** aipipe filtra por **equipo de Linear**, no por proyecto ni por repositorio. Si dos
repositorios comparten el mismo equipo y hay un `watch` en cada uno, cualquiera de los dos puede recoger cualquier ticket
con `ai-ready` y trabajar en el repositorio equivocado. Mientras tanto: un equipo de Linear por repositorio, o un solo
`watch` y lanzar los demás con `aipipe run --issue`. Un filtro por proyecto de Linear está pendiente.

El límite global de ejecuciones simultáneas (`runner.max_concurrent`, por defecto 1) vale para toda la máquina, y nunca
hay dos ejecuciones en el mismo repositorio.

## 12. Buenas prácticas

- **Empieza pequeño.** Los primeros tickets, tareas de una función. Mira el comentario de resultado, el diff y `aipipe budget`.
- **Revisa siempre el diff antes del merge.** Los tests pasan, pero el revisor automático es otro modelo barato.
- **Si el mismo tipo de ticket falla varias veces,** el problema suele ser el ticket (criterios vagos) o el comando de
  tests. Arréglalo ahí antes de forzar `ai:heavy`.
- **Sube de tier con criterio.** `ai:heavy` usa un modelo mucho más caro en cupo: DeepSeek V4 Pro tiene un límite mensual
  de 15 $ dentro de Go, frente a 60 $ del tier ligero.
- **Aprovecha el horario valle de DeepSeek:** cuesta la mitad fuera de las horas pico (01–04 y 06–10 UTC, de lunes a
  viernes). Lanza lotes grandes por la tarde o en fin de semana.
- **Proyectos Node u otros con dependencias:** el `git worktree` no trae `node_modules`. Pon en `test_command` algo como
  `npm ci && npm test`, o los tests fallarán siempre.
- **No pongas modelos «Contributor»** (Muse Spark): entrenan con tus prompts. `aipipe doctor` avisa si los configuras.

## 13. Ejemplo real

Primera ejecución real de aipipe contra Linear y Go, en el VPS, sobre un repositorio de prueba (5-oct-2026). El ticket
pedía añadir `multiply(a, b)` a `calc.py` con su test. Sin etiquetas de punto de control.

```text
$ aipipe run --issue ALB-32
[ALB-32] [Prueba aipipe] Añadir función multiply a calc.py con su test
  - aipipe-triage (opencode-go/mimo-v2.6-flash)
  ruta: light (triage: complejidad 1)
 intento 1/3 en tier light
  - aipipe-impl-light (opencode-go/glm-5.3-flash)
   tests: ok
  - aipipe-review (opencode-go/glm-5.3)
  review: aprobado

Resumen:
  ALB-32: done (tier light, 1 intento(s), $0.0108) rama ai/alb-32-prueba-aipipe-anadir-funcion-multiply-a
```

En Linear, la issue pasó de *Todo* a *In Progress* y a *In Review* en unos 5 minutos, y aipipe dejó este comentario:

```markdown
**aipipe: listo para revision**
- Tier final: `light` (opencode-go/glm-5.3-flash), intentos: 1
- Coste estimado a precio de lista: $0.0108 (la cifra autoritativa es la consola de Go)
- Rama: `ai/alb-32-prueba-aipipe-anadir-funcion-multiply-a`
- Trabajo commiteado en la rama local `ai/alb-32-prueba-aipipe-anadir-funcion-multiply-a` (sin push: project.push=false o sin remoto).
```

Fíjate en que el triaje clasificó el ticket como trivial (complejidad 1) y por eso **no hubo plan**. Para forzar un plan
y la aprobación, el ticket necesita `ai:approve`.
