# Instalación en un VPS

De un servidor vacío a aipipe funcionando, accesible solo por Tailscale. Está escrita a partir de la instalación real
que se hizo el 5-oct-2026 (Contabo, Ubuntu 26.04.1 LTS, OpenCode 1.18.34, Python 3.14), incluidos los tropiezos, que
aparecen marcados con **⚠**. Cuenta con unas 2 horas la primera vez.

Convención de las cajas de comandos: el comentario inicial dice **dónde** se ejecuta cada bloque.

| Dónde | Quién es |
|---|---|
| `[PC]` | PowerShell en tu ordenador |
| `[VPS root]` | Sesión inicial en el servidor, como `root` (solo al principio) |
| `[VPS albert]` | Tu usuario administrador (con sudo) |
| `[VPS aipipe]` | El usuario sin privilegios que ejecuta a los agentes |

## 0. Qué vas a tener al final

- Un servidor con **un solo usuario administrador** (`albert`), sin acceso como root ni por contraseña.
- Cortafuegos con **todo el tráfico entrante cerrado**; SSH solo por la red privada de Tailscale.
- Un usuario `aipipe` **sin sudo** que ejecuta OpenCode y aipipe.
- Las claves guardadas en archivos con permisos `600`.

## 1. Elegir el servidor

Mínimo razonable: 2 vCPU, 4 GB de RAM y 40 GB de disco. Recomendado: **4 vCPU, 8 GB, 100 GB**, que da holgura para
compilar, ejecutar tests y guardar varios repositorios y `git worktree`. Se usó un Cloud VPS 4 de Contabo (Alemania,
5,50 €/mes). Elige una imagen **limpia** de Ubuntu LTS, con tu clave SSH ya cargada si el proveedor lo permite.

## 2. Acceso por SSH con clave (desde Windows)

Si no tienes clave, créala en tu PC. Una sola vez:

```powershell
# [PC]
ssh-keygen -t ed25519 -C "albert@mi-pc"
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub
```

La segunda línea muestra la **clave pública** (una línea que empieza por `ssh-ed25519`). Esa es la que pegas en el panel
del proveedor. **Nunca compartas el archivo sin `.pub`**: es la clave privada. El texto final de la línea
(`albert@mi-pc`) es solo una etiqueta y el servidor lo ignora.

Conecta con la IP de tu servidor:

```powershell
# [PC]
ssh root@IP_DEL_SERVIDOR
```

**⚠ `REMOTE HOST IDENTIFICATION HAS CHANGED`:** sale si reinstalaste el sistema del servidor (cambia su huella). Se quita
la entrada vieja con `ssh-keygen -R IP_DEL_SERVIDOR` y se vuelve a conectar.

**⚠ `Permission denied` pidiendo contraseña de root:** el servidor no tiene tu clave. Reinstala el sistema desde el
panel eligiendo tu clave SSH, o añádela a `/root/.ssh/authorized_keys` desde la consola web del proveedor.

## 3. Usuario administrador

```bash
# [VPS root]
apt update && apt full-upgrade -y
adduser --gecos "" albert                  # te pide una contraseña: la usarás para sudo
usermod -aG sudo albert
install -d -m 700 -o albert -g albert /home/albert/.ssh
cp /root/.ssh/authorized_keys /home/albert/.ssh/authorized_keys
chown albert:albert /home/albert/.ssh/authorized_keys
chmod 600 /home/albert/.ssh/authorized_keys
wc -c /home/albert/.ssh/authorized_keys    # debe ser > 0
```

**⚠ Comprueba que `authorized_keys` NO está vacío (`wc -c`) y prueba el login desde tu PC antes de seguir.** En la
instalación real el archivo se creó con 0 bytes, y el fallo no se vio hasta después de cerrar el SSH por contraseña,
cuando ya solo se podía entrar desde una sesión abierta.

```powershell
# [PC] en una ventana NUEVA, sin cerrar la sesión de root
ssh albert@IP_DEL_SERVIDOR
```

Debe entrar sin pedir contraseña. Si no, no sigas: arréglalo desde la sesión de root que sigues teniendo abierta.

## 4. Endurecer SSH

El nombre del archivo importa: SSH usa el **primer** valor que lee, y `01-` va antes del archivo que suelen dejar los
proveedores (`50-cloud-init.conf`), que activa la contraseña.

```bash
# [VPS albert]
sudo tee /etc/ssh/sshd_config.d/01-hardening.conf >/dev/null <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
EOF
sudo sshd -t && echo "SINTAXIS OK"
sudo sshd -T | grep -Ei '^(permitrootlogin|passwordauthentication|kbdinteractiveauthentication|pubkeyauthentication) '
```

Debe salir `SINTAXIS OK` y `permitrootlogin no`, `passwordauthentication no`, `kbdinteractiveauthentication no`,
`pubkeyauthentication yes`. Si `passwordauthentication` sigue en `yes`, busca quién lo fuerza con
`sudo grep -ri passwordauth /etc/ssh/`. Aplica el cambio **sin cerrar la sesión actual**:

```bash
sudo systemctl reload ssh
```

Comprueba desde otra ventana:

```powershell
# [PC]
ssh albert@IP_DEL_SERVIDOR                           # entra
ssh root@IP_DEL_SERVIDOR                             # Permission denied (publickey)
ssh -o PubkeyAuthentication=no albert@IP_DEL_SERVIDOR  # Permission denied (publickey)
```

## 5. Cortafuegos y actualizaciones automáticas

El orden importa: **primero se permite SSH y después se activa**.

```bash
# [VPS albert]
sudo apt install -y ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw enable                    # responde y; el 22 ya está permitido, no se corta la sesión
sudo ufw status verbose

sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -f noninteractive unattended-upgrades
cat /etc/apt/apt.conf.d/20auto-upgrades     # Update-Package-Lists "1"; y Unattended-Upgrade "1";
sudo ss -tlnp                               # solo debe escuchar SSH (22) y DNS local (127.0.0.x)
```

Comprueba desde fuera que el resto de puertos no responden:

```powershell
# [PC]  (los puertos cerrados tardan unos segundos en dar timeout)
Test-NetConnection IP_DEL_SERVIDOR -Port 22      # True
Test-NetConnection IP_DEL_SERVIDOR -Port 80      # False
Test-NetConnection IP_DEL_SERVIDOR -Port 3306    # False
```

## 6. Tailscale: red privada

1. Crea una cuenta en tailscale.com (gratis para uso personal) y **activa la verificación en dos pasos** en la cuenta
   (Google o GitHub) con la que entras: quien la controle, controla el acceso al servidor.
2. Instala Tailscale en el servidor:

   ```bash
   # [VPS albert]
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up            # imprime una URL: ábrela en tu navegador e inicia sesión
   tailscale ip -4              # tu IP 100.x.x.x
   systemctl is-enabled tailscaled && systemctl is-active tailscaled   # enabled y active
   ```

3. Instala la app de Tailscale en tu **PC** y en el **móvil**, con **la misma cuenta**. Sin eso no ven el servidor.
4. Entra por la IP de Tailscale y **comprueba que realmente vas por ahí**:

   ```powershell
   # [PC]
   ssh albert@100.x.x.x
   ```

   ```bash
   # [VPS albert] dentro de esa sesión
   echo $SSH_CONNECTION       # debe empezar por 100. y el tercer campo ser tu IP 100.x del servidor
   ```

5. Permite SSH por Tailscale en el cortafuegos y, una vez comprobado el paso anterior, **cierra el SSH público**:

   ```bash
   # [VPS albert]
   sudo ufw allow in on tailscale0 to any port 22 proto tcp
   sudo ufw delete allow OpenSSH           # borra las reglas IPv4 e IPv6 del 22 público
   sudo ufw status verbose                 # solo deben quedar las reglas de tailscale0
   ```

   ```powershell
   # [PC] en una ventana nueva
   ssh albert@100.x.x.x                                   # entra
   Test-NetConnection IP_DEL_SERVIDOR -Port 22            # False
   ```

   **Plan de rescate:** si Tailscale falla en el servidor, desde una sesión que siga abierta `sudo ufw allow OpenSSH`
   reabre el acceso público. Si ya perdiste todas las sesiones, la **consola VNC del panel del proveedor** entra sin
   pasar por SSH.

6. En el panel de Tailscale (login.tailscale.com/admin): en *Machines → tu servidor → ⋯ → Disable key expiry* (si no, el
   servidor desaparece de tu red a los 180 días), y en *Users* comprueba que solo estás tú.

## 7. Usuario sin privilegios para los agentes

Los agentes ejecutan comandos de shell a partir de texto de un ticket. Corren con un usuario **sin sudo**, para que el
peor caso sea perder el contenido de su carpeta y no el servidor.

```bash
# [VPS albert]
sudo adduser --disabled-password --gecos "" aipipe
id aipipe                       # no debe aparecer el grupo sudo
sudo -iu aipipe                 # cambia a ese usuario (prompt aipipe@…)
```

A partir de aquí, mientras no se diga otra cosa, `[VPS aipipe]`. Su carpeta es privada (`750`), así que `albert` **no
puede ni listar** `/home/aipipe` sin sudo: es normal y es lo que se busca.

## 8. OpenCode y el plan Go

```bash
# [VPS albert]  (una vez, para tener lo necesario)
sudo apt install -y unzip python3-venv python3-pip git bubblewrap
python3 --version               # 3.11 o superior
bwrap --version                 # el sandbox de los agentes (ALB-27)
```

```bash
# [VPS aipipe]
curl -fsSL https://opencode.ai/install | bash
source ~/.bashrc
opencode --version
opencode auth login             # elige OpenCode Go y pega tu clave
ls -l ~/.local/share/opencode/auth.json     # debe ser -rw-------
opencode models | grep opencode-go
opencode run -m opencode-go/minimax-m3 "responde solo: ok" </dev/null
```

- **⚠ El `</dev/null` es obligatorio** cada vez que ejecutes `opencode run` desde un script, cron o servicio: si hereda
  un stdin abierto, se bloquea esperando entrada. aipipe ya lo hace por dentro.
- **Antes de seguir:** en tu cuenta de OpenCode (opencode.ai), comprueba que **«Use balance» está desactivado**. Es lo que
  garantiza que nunca se cobre más que la suscripción de 10 $.
- Si el instalador no deja `opencode` en el `PATH`, cierra la sesión con `exit` y vuelve a entrar con `sudo -iu aipipe`.

## 9. Instalar aipipe

Sube el zip desde tu PC (la carpeta de descargas, donde esté `aipipe.zip`) y entrégaselo al usuario `aipipe`:

```powershell
# [PC]
scp aipipe.zip albert@100.x.x.x:/tmp/
```

```bash
# [VPS albert]  ⚠ con sudo: sin él, «install» falla porque albert no puede escribir en /home/aipipe
sudo install -o aipipe -g aipipe -m 644 /tmp/aipipe.zip /home/aipipe/aipipe.zip
sudo ls -l /home/aipipe/aipipe.zip
sudo -iu aipipe
```

```bash
# [VPS aipipe]
unzip -q aipipe.zip
python3 -m venv ~/venv
~/venv/bin/pip install ./aipipe
echo 'export PATH=$HOME/venv/bin:$PATH' >> ~/.bashrc && source ~/.bashrc
aipipe --version
aipipe install-agents
ls ~/.config/opencode/agents          # ⚠ «agents» en plural: aipipe-impl-*.md, -plan, -review, -triage
git config --global user.name "aipipe"
git config --global user.email "aipipe@vps.local"
```

## 10. Conectar con Linear

1. En Linear: *Settings → Account → Security & access → Personal API keys → New API key*. Copia la clave
   (`lin_api_…`); **solo se muestra una vez**. Si la pierdes, crea otra y revoca la anterior.
2. Guárdala en un archivo privado, sin que quede en el historial ni se vea en pantalla:

   ```bash
   # [VPS aipipe]
   mkdir -p ~/.config/aipipe
   read -rsp "Pega la clave de Linear y pulsa Enter: " K; echo
   ```

   **⚠ Espera a que la terminal se quede esperando y entonces pega la clave** (clic derecho en PowerShell, o
   `Ctrl+Shift+V`). No verás nada al pegar: es a propósito. Si pegas antes de que `read` esté esperando, la variable
   queda vacía y las comprobaciones posteriores fallan sin mensaje claro.

   ```bash
   printf 'LINEAR_API_KEY=%s\n' "$K" > ~/.config/aipipe/env
   unset K
   chmod 600 ~/.config/aipipe/env
   awk -F= '{print $1 " -> " length($2) " caracteres"}' ~/.config/aipipe/env   # unos 40 a 50
   ```

   **Nunca pegues la clave en un chat, un ticket ni un commit.** Si se te escapa, revócala en Linear y crea otra.

3. En Linear, crea en tu equipo las etiquetas **`ai-ready`** y **`ai:approve`** (y, si las vas a usar, `ai:light`,
   `ai:std` y `ai:heavy`). Las etiquetas de fase y fallo las crea aipipe.
4. Si los estados de tu equipo no se llaman `Todo`, `In Progress` e `In Review`, ajústalos en `.aipipe.toml`.

Para cargar la clave en una sesión (no la metemos en `.bashrc` a propósito):

```bash
set -a; source ~/.config/aipipe/env; set +a
```

## 11. Un repositorio de pruebas

Es un proyecto mínimo y sin valor, con un test, para validar el flujo sin riesgo. `push` y `pr` van desactivados: de
momento nada sale del servidor.

```bash
# [VPS aipipe]
~/venv/bin/pip install -q pytest
mkdir -p ~/work/sandbox && cd ~/work/sandbox
git init -q -b main
printf 'def add(a, b):\n    return a + b\n' > calc.py
printf 'from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n' > test_calc.py
cat > .aipipe.toml <<'EOF'
[linear]
team = "ALB"            # la clave de tu equipo en Linear

[project]
base_branch = "main"
test_command = "pytest -q"
push = false
pr = false
EOF
printf '__pycache__/\n.pytest_cache/\n' > .gitignore
pytest -q
git add -A && git commit -q -m "proyecto de pruebas" && git log --oneline
```

### Activar el sandbox (ALB-27)

Con el sandbox, los agentes y los tests no pueden leer la clave de Linear. Se activa en la configuración global del
usuario `aipipe` y se verifica antes del primer ticket:

```bash
# [VPS aipipe]
cat >> ~/.config/aipipe/config.toml <<'EOF'

[sandbox]
mode = "bwrap"          # obligatorio: si bubblewrap no funciona, aipipe se niega a ejecutar (no degrada en silencio)
EOF
cd ~/work/sandbox
set -a; source ~/.config/aipipe/env; set +a
aipipe sandbox-check --opencode
```

Todas las líneas deben salir `OK`. Si sale `ERR` en «sandbox activo», mira
[solución de problemas](07-solucion-de-problemas.md#9-sandbox-bubblewrap): lo más probable es que el kernel de Ubuntu
restrinja los espacios de nombres de usuario. Si `config.toml` ya tenía una sección `[sandbox]`, edítala en vez de añadir
otra.

## 12. Comprobar y lanzar el primer ticket

```bash
# [VPS aipipe]
cd ~/work/sandbox
set -a; source ~/.config/aipipe/env; set +a
aipipe doctor
aipipe run --dry-run
```

- **⚠ Ejecuta `doctor` dentro del repositorio** y con la clave cargada. Desde la carpeta personal salen varios `ERR`
  (sin repositorio, sin configuración, sin `linear.team`, sin clave) que no son fallos de la instalación.
- Solo `gh` (si `pr=false`), CodeGraph y Graft pueden salir como `--` sin que importe.
- `aipipe run --dry-run` consulta Linear de verdad sin ejecutar nada. Sin tickets con `ai-ready` debe decir
  «No hay tickets listos». Un error de autenticación (`401`) significa clave mal guardada o revocada.

Después, crea un ticket pequeño (la plantilla está en la [guía de uso](01-guia-de-uso.md)), ponle `ai-ready` y ejecuta
`aipipe run`. Para validar también la aprobación desde el móvil, ponle además `ai:approve`: debe publicar el plan y
detenerse, esperar tu comentario `aprobado` y continuar al volver a ejecutar `aipipe run`.

## 13. Siguientes pasos

1. **Dejarlo corriendo solo:** servicio systemd con `aipipe watch` ([Operación](06-operacion.md)). Sin esto, el flujo
   desde el móvil no se reanuda por sí mismo.
2. **Cerrar lo pendiente de seguridad** antes de usarlo en proyectos reales ([Seguridad](05-seguridad.md)).
3. **Conectar GitHub** si quieres PRs: instalar `gh`, autenticar con un token limitado a los repositorios necesarios y
   poner `push = true` y `pr = true`. Sin validar todavía.

## Lista de comprobación final

- [ ] `ssh root@IP_PÚBLICA` no conecta, ni siquiera responde.
- [ ] `ssh albert@100.x.x.x` entra sin contraseña y `echo $SSH_CONNECTION` empieza por `100.`.
- [ ] `sudo ufw status verbose`: entrante denegado, solo `tailscale0` en el 22.
- [ ] `id aipipe` no incluye `sudo`.
- [ ] `ls -l ~/.local/share/opencode/auth.json` y `~/.config/aipipe/env` son `-rw-------`.
- [ ] «Use balance» desactivado en OpenCode.
- [ ] `aipipe doctor` sin `ERR` dentro del repositorio de pruebas.
- [ ] Un ticket de prueba llega a *In Review* y el diff es correcto.
