# Solución de problemas

Organizado por síntoma. Casi todo lo que aparece aquí **ocurrió de verdad** durante la instalación y la primera
ejecución (5-oct-2026), así que son los tropiezos más probables. Primero, tres comandos que resuelven la mitad de los
casos:

```bash
cd ~/work/<repo>                          # ⚠ siempre desde el repositorio
set -a; source ~/.config/aipipe/env; set +a
aipipe doctor
```

Para el flujo normal, [01-guia-de-uso.md](01-guia-de-uso.md); para el servidor, [06-operacion.md](06-operacion.md).

## Índice

1. [Acceso al servidor (SSH, Tailscale, ufw)](#1-acceso-al-servidor)
2. [Instalación (permisos, rutas, OpenCode)](#2-instalación)
3. [`aipipe doctor` da errores](#3-aipipe-doctor-da-errores)
4. [El ticket no se recoge](#4-el-ticket-no-se-recoge)
5. [Aprobación y cancelación](#5-aprobación-y-cancelación)
6. [Códigos de salida, cola y presupuesto](#6-códigos-de-salida-cola-y-presupuesto)
7. [Una ejecución falla o se queda colgada](#7-una-ejecución-falla-o-se-queda-colgada)
8. [Resultado: ramas, worktrees, git y GitHub](#8-resultado-ramas-worktrees-git-y-github)
9. [Sandbox (bubblewrap)](#9-sandbox-bubblewrap)

## 1. Acceso al servidor

### `Permission denied (publickey)` al entrar como `albert`

La clave pública no está en `/home/albert/.ssh/authorized_keys` o el archivo está **vacío** (pasó: medía 0 bytes).

1. Entra por otra vía (consola VNC de Contabo, o como `root` si aún lo permite).
2. `sudo ls -la /home/albert/.ssh` → `authorized_keys` debe pesar > 0 bytes y ser de `albert` con permisos 600; el
   directorio, 700.
3. Pega la clave **pública** (la línea `ssh-ed25519 AAAA… comentario`, nunca la privada) y comprueba la huella:
   `ssh-keygen -lf /home/albert/.ssh/authorized_keys` debe coincidir con `ssh-keygen -lf ~/.ssh/id_ed25519.pub` en tu PC.
4. `sudo tail -n 30 /var/log/auth.log` dice por qué se rechaza (`Authentication refused: bad ownership or modes`,
   «no matching key», etc.).

### `WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!`

Pasa tras reinstalar el servidor o al conectar por otra IP. Si sabes que es así, borra la entrada antigua:
`ssh-keygen -R <ip>` y vuelve a conectar. Si **no** has tocado nada, no continúes y revisa el servidor.

### Ya no entro por la IP pública

Es lo esperado: `ufw` solo deja `22/tcp` por `tailscale0` y no hay contraseñas. Entra con la IP de Tailscale
(`100.65.115.105`) con Tailscale activo en tu PC. «No tengo Tailscale en el PC» → instálalo e inicia sesión con la misma
cuenta que el servidor; `tailscale ip -4` en el VPS da su dirección, y `tailscale status` lista los dispositivos.

### Me quedé fuera del servidor

Consola VNC del panel de Contabo, `albert`, y: `sudo ufw allow OpenSSH` (temporal; ciérralo después con
`sudo ufw delete allow OpenSSH`). Detalle en [06-operacion.md §8](06-operacion.md).

### `ufw status` dice `inactive`

Activarlo **después** de añadir la regla de SSH, o te quedas fuera. Orden correcto en
[02-instalacion-vps.md](02-instalacion-vps.md) (primero `allow 22/tcp on tailscale0`, luego `enable`).

## 2. Instalación

| Síntoma | Causa y solución |
|---|---|
| `install: cannot remove … Operation not permitted` / `cannot create regular file` | `albert` no puede escribir en `/home/aipipe` (permisos 750, a propósito). Usa **`sudo install -o aipipe -g aipipe …`** |
| `ls /home/aipipe`: `Permission denied` | Lo mismo: es lo esperado para `albert` sin sudo. Usa `sudo ls` o entra como `aipipe` |
| `ls ~/.config/opencode/agent` → no existe | El directorio es **`agents`**, en plural |
| `opencode run` se queda parado sin salida | Falta `</dev/null`: OpenCode espera entrada por stdin. Siempre `opencode run … </dev/null` |
| `opencode: command not found` | El instalador no dejó `~/.opencode/bin` en el `PATH`. Cierra sesión (`exit`) y vuelve con `sudo -iu aipipe`; o añade `export PATH=$HOME/.opencode/bin:$PATH` |
| `aipipe: command not found` | Falta el venv en el `PATH`: `export PATH=$HOME/venv/bin:$PATH` (o `~/venv/bin/aipipe`) |
| `pip` dice «externally-managed-environment» | Estás instalando en el Python del sistema. Usa el venv (`~/venv/bin/pip …`) |
| Python demasiado antiguo | aipipe necesita ≥ 3.11 (sin dependencias externas); el servidor trae 3.14 |
| El modelo no existe / `ProviderModelNotFoundError` | `opencode models \| grep opencode-go`; ajusta `[models]` y `aipipe install-agents --force` |
| Falla la clave de Go | `ls -l ~/.local/share/opencode/auth.json` (600); repite `opencode auth login`; prueba con `opencode run -m opencode-go/minimax-m3 "responde ok" </dev/null` |

## 3. `aipipe doctor` da errores

`doctor` comprueba el entorno y es lo primero que se mira. Las causas habituales:

| Mensaje / síntoma | Causa y solución |
|---|---|
| Errores sobre «no es un repositorio git» o sobre `.aipipe.toml` | Lo ejecutaste **fuera del repositorio**. `cd ~/work/<repo>` |
| `LINEAR_API_KEY` ausente o vacía | No cargaste el entorno: `set -a; source ~/.config/aipipe/env; set +a`. Si el archivo está vacío (`awk -F= '{print $1" -> "length($2)}' ~/.config/aipipe/env` da 0 caracteres), pegaste la clave **antes** de que `read -rsp` estuviera esperando: repite el paso |
| Linear responde 401/«Authentication» | Clave revocada o mal copiada. Crea otra (ver [06, §5](06-operacion.md)) |
| No se ve el equipo (`team`) | `team` en `.aipipe.toml` debe ser la **clave** del equipo (`ALB`), no su nombre |
| Faltan estados `Todo` / `In Progress` / `In Review` | Tus estados se llaman distinto: ajusta `[linear]` ([03](03-configuracion.md)) |
| `opencode` no se encuentra | Ver §2 |
| Faltan agentes | `aipipe install-agents` (por defecto globales en `~/.config/opencode/agents/`) |
| `gh` no encontrado | Solo importa si `project.pr = true`. Si no, ignóralo |

**No puedo ver la API key en Linear.** Las claves personales solo se muestran al crearlas. Crea una nueva
(*Settings → Account → Security & access → Personal API keys*), guárdala con el procedimiento de la instalación y revoca
la antigua.

**Pegué la clave en un chat o un ticket.** Revócala ya, aunque creas que nadie la vio, y crea otra.

## 4. El ticket no se recoge

`aipipe run` dice: *«No hay tickets listos (etiqueta/estado de disparo). Nada que hacer.»* Revisa **todo** esto:

1. **Etiqueta.** El ticket necesita `ai-ready` (el nombre exacto de `trigger_label`).
2. **Estado.** Debe estar en un estado de `trigger_states` (por defecto *Todo*). Un ticket en *Backlog*, *In Progress*,
   *In Review* o *Done* no se recoge.
3. **Equipo y proyecto.** Solo se leen los tickets del equipo configurado (`team = "ALB"`) y, si defines
   `project = "..."` en `[linear]`, solo los de ese proyecto. Ver varios repositorios en
   [01-guia-de-uso.md](01-guia-de-uso.md#11-varios-proyectos).
4. **Creador.** Solo se ejecutan los tickets **creados por el dueño de la API key** (o por `allowed_creators`). Si creó el
   ticket otra persona, una integración o un usuario borrado, se descarta con un aviso. `aipipe doctor` muestra la política
   activa. No actives `allow_any_creator` para «arreglarlo»: léelo en [05-seguridad.md](05-seguridad.md).
5. **Etiqueta duplicada.** Si existen dos etiquetas `ai-ready` (workspace y equipo), comprueba que has puesto la que ve tu equipo.
6. **Ya está en curso o esperando.** Un ticket con `ai-waiting` no es «nuevo»: aipipe solo mira si respondiste.
7. **`--issue`.** `aipipe run --issue ALB-33` ignora la etiqueta de disparo, pero **no** el filtro de creador.

Para ver qué haría sin gastar nada: `aipipe run --dry-run`.

## 5. Aprobación y cancelación

### Comenté «aprobado» y no pasa nada

Primero: **hay que volver a ejecutar** `aipipe run` (o tener `watch` en marcha). La aprobación no dispara nada por sí
sola; se lee en la siguiente pasada. Si aun así no la reconoce, se debe a una de estas reglas (las cumplen todas a la vez):

- El comentario es **posterior** al comentario del plan. Uno escrito antes (o el propio texto del plan) no cuenta.
- Lo escribe un **usuario autorizado**: el dueño de la clave o `allowed_creators`. Los comentarios de otros se ignoran.
- Su **cuerpo entero** es una respuesta válida: `aprobado`, `ok`, `vale`, `adelante`, `lgtm`, `dale`, `👍` o `✅`.
  «aprobado, pero cambia X» **no** vale. Mejor `cambios: <texto>`.
- Para pedir cambios: `cambios: <qué cambiar>`. Para parar: `rechazado`, `cancelar` o `stop`.

Si el ticket ya no aparece en la cola de espera, mira `ls ~/.local/state/aipipe/waiting/` (debe existir `<ID>.json`).
Si **falta** (por ejemplo, lo borraste), no hay plan que aprobar: quita `ai-waiting`, ponle `ai-ready` y vuelve a
empezar desde *Todo*.

### El ticket no pide aprobación

Solo la piden los tickets con la etiqueta `ai:approve` (con `after_plan = "label"`, el valor por defecto) o todos si
`after_plan = "always"`. Con `after_plan = "never"` no la pide ninguno. Comprueba que la etiqueta está puesta **antes**
de la primera pasada y que se llama exactamente igual que `approve_label`. Un ticket que no la tenía no vuelve atrás: si
ya empezó, déjalo terminar o cancélalo y relánzalo con la etiqueta.

Puedes pedir cambios al plan hasta `max_plan_revisions` veces (2 por defecto); a partir de ahí solo cabe aprobar o rechazar.

### Cancelar un ticket en curso

Mueve la issue **fuera de *In Progress*** (a *Todo*, *Backlog*, *Cancelled*…). aipipe lo detecta en `cancel_check_s`
(≈ 20 s), mata al agente y a sus procesos hijos y quita sus etiquetas. Si no ocurre:

- ¿Sigue realmente en *In Progress*? Linear a veces tarda en reflejar el cambio desde el móvil; refresca.
- Un fallo puntual de Linear **nunca se toma por cancelación**; si el sondeo no puede consultar, el ticket sigue.
- Comprueba que el proceso existe (`pgrep -fa 'aipipe|opencode'`) y, si hace falta, la parada de emergencia de
  [06-operacion.md §6](06-operacion.md).

## 6. Códigos de salida, cola y presupuesto

| Código | Significa | Qué hacer |
|---|---|---|
| 0 | Todo bien (o nada que hacer) | — |
| 1 | Algún ticket falló | Leer el comentario del ticket y §7 |
| 2 | Error de configuración o de Linear | `aipipe doctor`; red; clave |
| 3 | **Cola pausada por presupuesto** | Esperar a la hora indicada (`reanuda aprox. …`) o ajustar el reparto; ver `aipipe budget` |
| 4 | **En cola**: hay otra ejecución en el mismo repositorio, o se alcanzó `max_concurrent` | Nada falló. Espera a que termine la otra; el ticket sigue en la cola |

- **«hay otra ejecución en curso en este repositorio»** (código 4): dos tickets nunca corren a la vez en un mismo
  repositorio, y por defecto `max_concurrent = 1`. Es intencionado (el cupo de Go se gasta el doble de rápido con dos
  agentes). Si un bloqueo quedó huérfano, ver §7.
- **Pausa por presupuesto:** los umbrales por ventana (5 h, semana, mes) están en `[budget]`. Un ticket que se pausa
  vuelve a *Todo* con un comentario explicativo; si ya tenía el plan aprobado, **vuelve a esperar** (no hace falta
  aprobarlo de nuevo). `aipipe budget` enseña cuánto queda y cuándo se reanuda.
- **La estimación local no coincide con la consola de Go:** es solo una estimación. Anota diferencias con
  `aipipe budget --add-usd <importe> --note "…"`.
- **El precio de un modelo no está en la tabla** (`[pricing]`): se usa una tarifa por defecto prudente; añádelo para
  que el gasto se estime bien.

## 7. Una ejecución falla o se queda colgada

### El ticket acaba con `ai-failed`

Es el comportamiento previsto: el ticket vuelve a *Todo*, se le quita `ai-ready` y se añade `ai-failed`, con un comentario
que explica el motivo (tests, revisión, tiempo máximo, error de OpenCode…). Para reintentar: arregla la causa (a menudo
conviene **precisar el ticket**), quita `ai-failed`, ponle `ai-ready` y lánzalo. Mira también el ledger
(`tail ~/.local/share/aipipe/ledger.jsonl`) y el worktree conservado
(`~/.local/share/aipipe/worktrees/<repo>/<id>`), donde puedes ejecutar los tests a mano.

| Causa frecuente | Solución |
|---|---|
| Los tests fallan por entorno (dependencias que faltan: `pytest`, `node_modules`…) | Instala lo necesario **para el usuario `aipipe`** (el worktree no hereda tu entorno). En Python, `~/venv/bin/pip install pytest`; en Node, instala dentro del worktree o pon la instalación en `test_command` |
| `test_command` mal escrito o ausente | Revisa `.aipipe.toml`. Se ejecuta con `shell=True` en el worktree |
| El revisor pide cambios en bucle (3 intentos, escalada tras 2) | Ticket demasiado grande o ambiguo: divídelo o añade criterios de aceptación concretos |
| Tiempo máximo del agente superado | Divide el ticket; sube el límite de su rol solo si está justificado |
| Dos tickets tocan los mismos archivos | Se ejecutan por separado, pero fusiona el primero antes de lanzar el segundo para evitar conflictos |

### Se queda colgado sin salida

- ¿`opencode run` sin `</dev/null`? aipipe ya lo lanza bien; solo pasa cuando lo ejecutas tú a mano.
- `pgrep -fa opencode`: si hay un proceso viejo que ya no avanza, mata el ticket por la vía normal (sácalo de
  *In Progress*) antes que con `kill`.
- Modelo saturado o red: reintenta más tarde; el ticket se reencola.

### Bloqueo huérfano

Un `Ctrl+C`, un reinicio o un `kill -9` pueden dejar archivos en `~/.local/state/aipipe/locks/`. aipipe descarta solo los
bloqueos de procesos que **ya no existen**. Si queda alguno:

```bash
ls ~/.local/state/aipipe/locks/
cat ~/.local/state/aipipe/locks/<archivo>.lock          # contiene un PID
ps -p <PID>                                              # si no existe, es huérfano
rm ~/.local/state/aipipe/locks/<archivo>.lock
```

## 8. Resultado: ramas, worktrees, git y GitHub

| Síntoma | Causa y solución |
|---|---|
| `git diff main..feat/…` no devuelve nada | El prefijo de las ramas es **`ai/`** (`branch_prefix`). Lista con `git branch --list 'ai/*'` |
| No encuentro el resultado | Con `push = false` vive solo en el servidor: rama `ai/<id>-<slug>` en `~/work/<repo>`. Revísala allí (`git log main..ai/…`, `git diff main..ai/…`); consulta [01-guia-de-uso.md](01-guia-de-uso.md) |
| `git worktree add` falla: «ya existe la rama» | Quedó una rama de un intento anterior: `git branch -D ai/<id>-<slug>` y `git worktree prune`. Si el worktree del mismo ticket existe, aipipe lo recrea solo |
| Worktrees sobrantes tras un fallo | Se conservan a propósito (`keep_worktree_on_fail`); límpialos ([06, §7](06-operacion.md)) |
| «gh no está instalado: no se puede crear el PR» | Instala `gh` y autentícalo, o pon `pr = false` |
| `git push` falla por autenticación | Sin credenciales de GitHub para `aipipe`. Crea un token de un solo repositorio y haz `gh auth login`; mientras tanto, `push = false` |
| `Author identity unknown` al hacer commit | Falta `git config --global user.name/user.email` para `aipipe` (paso 9 de la instalación) |
| La base no es `main` | Ajusta `base_branch` en `.aipipe.toml` |
| El agente modificó algo que no pedía | Rechaza la rama (`git branch -D …`), endurece el ticket (criterios y límites) y usa `ai:approve` para ver el plan antes |

## 9. Sandbox (bubblewrap)

Contexto en [03-configuracion.md](03-configuracion.md#sandbox) y [05-seguridad.md](05-seguridad.md). Empieza siempre por
`aipipe sandbox-check --opencode` (con la clave cargada): dice qué comprobación falla.

| Síntoma | Causa y solución |
|---|---|
| `sandbox.mode = 'bwrap' pero no esta disponible: 'bwrap' no esta instalado` | `sudo apt install -y bubblewrap` (como `albert`) |
| `bwrap: No permissions to create new namespace` o `setting up uid map: Permission denied` | El kernel o AppArmor impide los espacios de nombres de usuario sin privilegios (en Ubuntu: `kernel.apparmor_restrict_unprivileged_userns`). Mira `sysctl kernel.apparmor_restrict_unprivileged_userns` y `dmesg` filtrando por apparmor. Opciones: un perfil de AppArmor que permita `bwrap` (las versiones recientes de Ubuntu traen uno: comprueba que está activo) o relajar el sysctl (`sudo sysctl kernel.apparmor_restrict_unprivileged_userns=0`; es una decisión de seguridad tuya y afecta a todo el sistema). **No pongas `mode = "off"` para «arreglarlo»** en un servidor con repositorios reales |
| `run` termina con «Error de configuracion: … sandbox» (código 2) | `mode = "bwrap"` sin bubblewrap utilizable: es lo previsto, no degrada en silencio. Arregla lo anterior |
| Un aviso «sandbox no disponible … SIN aislamiento» | Estás en `mode = "auto"` y bubblewrap no funciona: los agentes corren sin aislamiento. En el VPS usa `mode = "bwrap"` |
| El agente o los tests dicen «command not found» o «No such file» dentro del sandbox | Una herramienta vive en tu HOME y no está en `ro_paths` (solo `~/.opencode`, los agentes de `~/.config/opencode` y `~/venv`). Añádela: `extra_ro = [".nvm", ".local/bin"]` |
| OpenCode falla con `FileSystem.writeFile (…)` | Algo que escribe no está en una ruta escribible. Mira la ruta del error y añádela a `extra_rw`. (`~/.config/opencode` ya es escribible pero efímero: solo sus agentes y su `opencode.json` están en solo lectura; si usas otra configuración de OpenCode, añádela a `extra_ro`) |
| Los tests de un proyecto fallan solo dentro del sandbox | Dependen de algo que no ven (variables que no están en `env_allow`, archivos de tu HOME, red con `tests_network = false`). Añade la variable a `env_allow_extra` o la ruta a `extra_ro`/`extra_rw` |
| Una variable de entorno «no llega» al agente | Solo llegan las de `env_allow`/`env_allow_extra`. `LINEAR_API_KEY`, `GH_TOKEN` y `GITHUB_TOKEN` no llegan nunca (a propósito) |
| `git commit` dentro del agente falla | A propósito: el agente no hace commit y el metadato de git es de solo lectura. Lo hace el runner |

## Si nada de esto ayuda

Reúne, **sin claves**:

```bash
aipipe --version
aipipe doctor
tail -n 20 ~/.local/share/aipipe/ledger.jsonl
journalctl -u aipipe-watch@<repo> -n 100 --no-pager     # solo si usas el servicio
```

y abre un ticket en Linear (proyecto *Linear Agent Runner*) con el síntoma, el comando exacto y lo que esperabas.
