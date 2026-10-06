# Operación

Manual del día a día del servidor: arrancar y parar el runner, rotar claves, actualizar, limpiar y recuperarse de un
reinicio o un problema. Todo se hace como el usuario `albert` (con `sudo`) o como `aipipe` (sin sudo); cada bloque dice
cuál. Para instalar desde cero, [02-instalacion-vps.md](02-instalacion-vps.md).

> **Estado:** lo manual (`aipipe run`, `aipipe watch` a mano) está validado. **El servicio systemd es una plantilla sin
> probar** (`deploy/aipipe-watch@.service`); pruébalo con un repositorio de prueba antes de fiarte de él.

## 1. Cómo se ejecuta aipipe

| Modo | Comando | Cuándo |
|---|---|---|
| Una pasada | `aipipe run` | Pruebas, primeras veces, o cuando quieres controlar tú el momento |
| Un ticket concreto | `aipipe run --issue ALB-32` | Repetir o forzar un ticket (ignora la etiqueta de disparo, no el filtro de creador) |
| Ensayo | `aipipe run --dry-run` | Ver qué cogería sin llamar a ningún modelo |
| Bucle | `aipipe watch --interval 300` | Operación normal: consulta Linear cada 5 minutos |
| Panel | `aipipe tui` | Ver el estado y lanzar acciones desde una pantalla (requiere `pip install aipipe[tui]`) |
| Servicio | `systemctl … aipipe-watch@<repo>` | Que sobreviva a reinicios y a cerrar la terminal |

Siempre desde el directorio del repositorio (`~/work/<repo>`) y con la clave de Linear cargada. La TUI carga sola la
clave desde `~/.config/aipipe/env`; el resto de comandos la necesitan en el entorno (`set -a; source ~/.config/aipipe/env; set +a`):

```bash
# [VPS aipipe]
cd ~/work/sandbox
set -a; source ~/.config/aipipe/env; set +a
aipipe doctor
aipipe run --dry-run
```

`watch` no tiene modo demonio propio: si cierras la terminal, muere. Para mantenerlo vivo sin systemd, usa `tmux`
(`sudo apt install tmux` como `albert`; luego `tmux new -s aipipe` como `aipipe`, lanza `watch` y sal con `Ctrl+B D`;
vuelves con `tmux attach -t aipipe`).

## 2. Servicio systemd (plantilla sin probar)

El archivo `deploy/aipipe-watch@.service` define **una instancia por repositorio**: `aipipe-watch@sandbox` vigila
`/home/aipipe/work/sandbox`.

```bash
# [VPS albert]
sudo cp ~aipipe/aipipe/deploy/aipipe-watch@.service /etc/systemd/system/    # o donde hayas dejado el archivo
sudo systemctl daemon-reload
sudo systemctl enable --now aipipe-watch@sandbox
systemctl status aipipe-watch@sandbox
journalctl -u aipipe-watch@sandbox -f          # registro en vivo (Ctrl+C para salir)
```

Parar, reiniciar, desactivar:

```bash
sudo systemctl stop aipipe-watch@sandbox       # mata también a los agentes en curso (KillMode=control-group)
sudo systemctl restart aipipe-watch@sandbox
sudo systemctl disable --now aipipe-watch@sandbox
```

Cosas a tener en cuenta:

- **El servicio no usa tu `.bashrc`.** El `PATH` (venv y `~/.opencode/bin`) y la clave de Linear (`EnvironmentFile`) van
  declarados en la unidad. Si algo funciona a mano y falla en el servicio, casi seguro es esto.
- **Si cambias la clave** en `~/.config/aipipe/env`, reinicia el servicio.
- **Parar el servicio a mitad de un ticket** deja la issue en *In Progress*. Al reiniciar, el bloqueo del ticket es de un
  proceso muerto (se detecta y se descarta), pero el ticket **no se retoma solo**: ver §6.
- **Seguridad:** `EnvironmentFile` pone `LINEAR_API_KEY` en el entorno del runner. Los agentes y los tests no la reciben
  (entorno por lista blanca y sandbox) **si `sandbox.mode = "bwrap"`**: comprueba `aipipe sandbox-check` con la clave
  cargada a mano antes de fiarte, y no uses el servicio con `mode = "off"`.

## 3. Estado y registros

| Qué | Dónde |
|---|---|
| Configuración global y local | `~/.config/aipipe/config.toml`, `.aipipe.toml` del repositorio |
| Clave de Linear | `~/.config/aipipe/env` (600) |
| Clave de Go | `~/.local/share/opencode/auth.json` (600) |
| Registro de gasto y ejecuciones | `~/.local/share/aipipe/ledger.jsonl` |
| Worktrees | `~/.local/share/aipipe/worktrees/<repo>/<id>` |
| Tickets esperando aprobación | `~/.local/state/aipipe/waiting/<ID>.json` |
| Bloqueos (ticket, repo, hueco global) | `~/.local/state/aipipe/locks/` |
| Ramas del resultado | rama `ai/<id>-<slug>` en `~/work/<repo>` |

Comprobaciones rápidas:

```bash
aipipe tui                                     # panel con todo lo de abajo en una pantalla (requiere `pip install aipipe[tui]`)
aipipe budget                                  # gasto estimado por ventana (5 h, semana, mes)
tail -n 20 ~/.local/share/aipipe/ledger.jsonl  # últimas ejecuciones
ls ~/.local/state/aipipe/waiting/              # tickets esperando tu «aprobado»
ls ~/.local/state/aipipe/locks/                # bloqueos activos
git -C ~/work/sandbox branch --list 'ai/*'     # ramas generadas
git -C ~/work/sandbox worktree list            # worktrees vivos
```

`aipipe budget` es una **estimación local**. La cifra real está en la consola de Go (OpenCode); contrástalas de vez en
cuando y, si difieren, anota la diferencia con `aipipe budget --add-usd <importe> --note "<motivo>"`.

## 4. Actualizar

### aipipe

Si aipipe está en GitHub ([08-github-y-servicio.md](08-github-y-servicio.md)), la forma normal es `bash ~/work/aipipe/deploy/update.sh`
(trae `main`, pasa los tests y solo entonces instala) y reiniciar el servicio. Lo de abajo es la vía manual con el zip.

```powershell
# [PC] desde la carpeta donde esté aipipe.zip
scp aipipe.zip albert@100.65.115.105:/tmp/
```

```bash
# [VPS albert]  (con sudo, como en la instalación)
sudo install -o aipipe -g aipipe -m 644 /tmp/aipipe.zip /home/aipipe/aipipe.zip
sudo -iu aipipe
```

```bash
# [VPS aipipe]
rm -rf ~/aipipe && unzip -q aipipe.zip
~/venv/bin/pip install --upgrade --force-reinstall ./aipipe
aipipe --version
aipipe install-agents --force        # solo si cambió la tabla de modelos o las plantillas de agentes
aipipe doctor
aipipe sandbox-check --opencode
```

Si usas el servicio: `sudo systemctl restart aipipe-watch@<repo>`. Hazlo con la cola vacía o sin tickets en curso. Los
tests solo se ejecutan en desarrollo, no hace falta en el servidor (la documentación tampoco).

### OpenCode

```bash
# [VPS aipipe]
opencode upgrade                     # o repite el instalador oficial
opencode --version
opencode models | grep opencode-go   # ¿siguen existiendo los 6 modelos de la tabla?
```

Si Go renombra o retira un modelo, `aipipe doctor` lo avisa. Cambia la tabla en `[models]`
([03-configuracion.md](03-configuracion.md)) y reinstala los agentes con `aipipe install-agents --force`.

### El sistema

Las actualizaciones de seguridad son automáticas (`unattended-upgrades`). Cada cierto tiempo, y siempre tras un
aviso de «reinicio necesario»:

```bash
# [VPS albert]
sudo apt update && sudo apt upgrade -y
[ -f /var/run/reboot-required ] && echo "hace falta reiniciar"
sudo reboot
```

Tras el reinicio, ver §6.

## 5. Rotar claves

Rota cualquier clave en cuanto sospeches que se vio (chat, captura, log) y, de todos modos, cada cierto tiempo.

### Linear

1. Linear → *Settings → Account → Security & access → Personal API keys*: crea una nueva.
2. En el VPS, sustituye el archivo (con el truco de `read -rsp`, [02, §10](02-instalacion-vps.md)):

   ```bash
   # [VPS aipipe]
   read -rsp "Pega la clave nueva y pulsa Enter: " K; echo
   printf 'LINEAR_API_KEY=%s\n' "$K" > ~/.config/aipipe/env; unset K
   chmod 600 ~/.config/aipipe/env
   set -a; source ~/.config/aipipe/env; set +a
   aipipe doctor                       # debe conectar con Linear
   ```
3. Reinicia el servicio si lo usas y **revoca la clave antigua** en Linear.

La clave nueva debe crearla **el mismo usuario** que creó los tickets: el filtro de creador compara con el dueño de la
clave (ver [01-guia-de-uso.md](01-guia-de-uso.md)). Si cambias de usuario, tus tickets antiguos dejan de ser ejecutables.

### Go (OpenCode)

1. Genera una clave nueva en tu cuenta de OpenCode y revoca la anterior.
2. `opencode auth login` como `aipipe` y pega la nueva.
3. `chmod 600 ~/.local/share/opencode/auth.json` y prueba:
   `opencode run -m opencode-go/minimax-m3 "responde ok" </dev/null` (⚠ el `</dev/null` evita que se quede esperando
   entrada).

### Claves SSH

Edita `/home/albert/.ssh/authorized_keys` (como `albert`) y deja solo las claves vigentes. **No cierres tu sesión
actual hasta verificar con otra terminal** que la clave nueva entra. Un archivo vacío te deja fuera (ocurrió en la
instalación).

### GitHub (cuando haya `push`/`pr`)

Token de acceso fino limitado a un repositorio, con permisos de contenido y PR y fecha de caducidad. Renueva con
`gh auth login` como `aipipe` y revoca el anterior en GitHub.

## 6. Recuperación

### Tras reiniciar el servidor

```bash
# [VPS albert]
tailscale status                                 # ¿conectado?
sudo ufw status                                  # activo, solo 22/tcp en tailscale0
systemctl status aipipe-watch@<repo>             # si usas el servicio
```

- Tailscale y `ufw` arrancan solos. Si no entras por SSH tras reiniciar, usa la consola VNC de Contabo (ver §8).
- **Tickets que estaban en *In Progress*:** aipipe no los retoma solos. Mira el ledger y la rama `ai/*` para ver hasta
  dónde llegó; después, o bien mueve la issue a *Todo* y deja que se recoja de nuevo, o bien lánzala a mano con
  `aipipe run --issue <ID>`. Si el worktree quedó a medias, se recrea (aipipe borra y vuelve a crear el del mismo ticket).
- **Tickets esperando aprobación** (`ai-waiting`): su estado está en `~/.local/state/aipipe/waiting/<ID>.json` y no se
  pierde con el reinicio; el ticket sigue esperando tu comentario.

### Un ticket atascado

| Síntoma | Qué hacer |
|---|---|
| Sigue en *In Progress* y no hay proceso | Mueve la issue a *Todo* y vuelve a lanzar. El bloqueo de un proceso muerto se descarta solo |
| Tiene `ai-failed` | Lee el comentario del fallo; corrige el ticket, quita `ai-failed`, ponle `ai-ready` y muévelo a *Todo* |
| Quedó esperando para siempre | Responde `aprobado`/`rechazado`; si el estado se corrompió, borra `~/.local/state/aipipe/waiting/<ID>.json` y relánzalo desde cero |
| Quieres parar uno en curso | Mueve la issue fuera de *In Progress* (la cancelación se nota en ≈ 20 s y mata al agente y a sus procesos) |
| Un bloqueo no se va | `ls ~/.local/state/aipipe/locks/`; si el PID del archivo no existe (`ps -p <pid>`), bórralo |

### Parada de emergencia

```bash
# [VPS albert]
sudo systemctl stop aipipe-watch@<repo>          # si usas el servicio
sudo pkill -u aipipe -f 'aipipe|opencode'        # mata lo que quede; es seguro, los tickets se pueden relanzar
```

Después, en Linear, saca de *In Progress* lo que estuviera en curso y revisa `aipipe budget`. Si la causa es una
sospecha de fuga, sigue [05-seguridad.md](05-seguridad.md) («Si sospechas que algo se ha filtrado»).

## 7. Mantenimiento

- **Worktrees sobrantes.** Se borran solos al terminar con éxito, en pausa o en espera. Tras un **fallo** se conservan
  (`keep_worktree_on_fail = true`) para que puedas inspeccionarlos. Cuando ya no los necesites:

  ```bash
  git -C ~/work/<repo> worktree list
  git -C ~/work/<repo> worktree remove --force ~/.local/share/aipipe/worktrees/<repo>/<id>
  git -C ~/work/<repo> worktree prune
  ```
- **Ramas `ai/*` ya fusionadas o descartadas:** `git branch -d ai/<id>-<slug>` (o `-D` para descartar).
- **Ledger.** Crece despacio (una línea por ejecución). Si lo recortas, hazlo con copia de seguridad: de él sale el cálculo
  del presupuesto. Un recorte que quite las últimas 4 semanas falsea la ventana mensual.
- **Disco.** `df -h /` y `du -sh ~/.local/share/aipipe ~/.cache 2>/dev/null`. `node_modules` o entornos de los worktrees
  pueden pesar bastante si usas proyectos grandes.
- **Copias de seguridad.** Lo valioso es poco y casi todo reproducible: el código vive en Git, los tickets en Linear, la
  configuración en `~/.config/aipipe/config.toml` y `.aipipe.toml`. Guarda una copia de `config.toml`, **nunca** de `env`
  ni de `auth.json` (esas se regeneran).

## 8. Acceso de emergencia

- **Perdiste SSH por Tailscale** (clave caducada, el cliente no conecta): entra por la **consola VNC** del panel de
  Contabo con el usuario `albert` y su contraseña de sistema (la de `sudo`).
- **Reabrir SSH público temporalmente** desde esa consola: `sudo ufw allow OpenSSH`. Tu clave SSH sigue siendo
  obligatoria (no hay contraseña por SSH). **Ciérralo en cuanto recuperes el acceso:** `sudo ufw delete allow OpenSSH`.
- **Error de configuración de SSH:** `sudo sshd -t` valida el archivo antes de `sudo systemctl restart ssh`. La
  configuración endurecida está en `/etc/ssh/sshd_config.d/01-hardening.conf`.

## 9. Lista de comprobación periódica

Semanal:

- [ ] `aipipe budget` y la consola de Go coinciden más o menos
- [ ] No hay tickets en *In Progress* sin actividad (`ls ~/.local/state/aipipe/locks/`)
- [ ] `ls ~/.local/state/aipipe/waiting/`: ¿hay algo esperándote?
- [ ] Revisados los diffs de las ramas `ai/*` antes de cualquier merge

Mensual:

- [ ] `sudo apt update && sudo apt upgrade`, y reiniciar si lo pide
- [ ] `aipipe doctor` y `aipipe sandbox-check` en verde, `opencode models` sigue listando los modelos de la tabla
- [ ] Dispositivos de Tailscale y miembros del workspace de Linear conocidos
- [ ] Limpiar worktrees y ramas viejas
- [ ] Revisar [05-seguridad.md](05-seguridad.md) por mejoras que se hayan cerrado
