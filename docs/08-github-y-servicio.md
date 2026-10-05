# GitHub, PR y servicio: trabajar con aipipe de verdad

Esta guía lleva aipipe del «funciona en el repositorio de pruebas» a **trabajar sobre un repositorio real**: aipipe sube
una rama por ticket, abre un PR, tú lo revisas y lo fusionas desde el móvil, y el servicio vigila Linear él solo. El
ejemplo es el caso en que **aipipe se desarrolla a sí mismo** (`albertsp/aipipe`), pero los pasos valen para cualquier
repositorio: cambia el nombre.

Requisitos: [instalación](02-instalacion-vps.md) terminada, el sandbox activo y verificado
(`aipipe sandbox-check --opencode` en verde) y [seguridad](05-seguridad.md) leída.

## Cómo queda el flujo

```
móvil: creas el ticket en Linear (ai-ready, y ai:approve si quieres ver el plan)
   └─► VPS (servicio aipipe watch): lo recoge, planifica, implementa en un worktree, pasa los tests y la revisión
         └─► git push de la rama ai/<ticket>-<slug>  +  gh pr create          (con el token de GitHub del runner)
               └─► Linear: comentario con el enlace del PR, el ticket pasa a In Review
móvil: revisas el PR en la app de GitHub y lo fusionas (o pides cambios)
   └─► el siguiente ticket parte del origin/main ya actualizado (aipipe hace fetch antes de cada worktree)
```

- **El token de GitHub lo usa el runner, no el agente.** El agente corre en el sandbox, que oculta `~/.config/gh`. El
  agente no hace commit ni push: lo hace aipipe cuando pasan los tests y la revisión.
- **aipipe solo sube ramas `ai/*`.** Nunca escribe en `main`. La integración la decides tú al fusionar el PR.
- **No hay fusión automática**, a propósito: la revisión del diff es la última barrera.

## 1. Crear el repositorio y el token (en GitHub, desde el móvil o el PC)

1. **Repositorio privado y vacío** (sin README ni licencia): `albertsp/aipipe`.
2. **Token de acceso fino** (*Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new
   token*):
   - *Resource owner:* tu usuario.
   - *Expiration:* 30 días (y renovarlo, [06-operación §5](06-operacion.md)).
   - *Repository access:* **Only select repositories** → solo este repositorio.
   - *Permissions → Repository permissions:* **Contents: Read and write** y **Pull requests: Read and write**
     (*Metadata: Read-only* se añade solo). Nada más.
   - Cópialo: solo se muestra una vez. **Nunca lo pegues en un chat, un ticket ni un commit.**
3. **Proteger `main`** si tu plan lo permite (*Settings → Rules → Rulesets*: exigir PR antes de fusionar). En repositorios
   privados del plan gratuito puede no estar disponible; no es imprescindible, porque aipipe nunca sube a `main`, pero es
   un buen cinturón de seguridad.

## 2. GitHub CLI en el VPS

```bash
# [VPS albert]
sudo apt install -y gh
gh --version
sudo -iu aipipe
```

```bash
# [VPS aipipe]
read -rsp "Pega el token de GitHub y pulsa Enter: " T; echo
printf '%s' "$T" | gh auth login --hostname github.com --git-protocol https --with-token
unset T
gh auth setup-git          # git usa gh como credencial: sin contraseñas en la URL
gh auth status             # debe decir «Logged in» y el alcance
```

**⚠ Espera a que la terminal esté esperando antes de pegar el token** (igual que con la clave de Linear). El token queda
en `~/.config/gh/hosts.yml`, que el sandbox oculta a los agentes.

## 3. Subir el código por primera vez (solo si el repositorio está vacío)

Si el código está aún en `~/aipipe` (el que descomprimiste del zip):

```bash
# [VPS aipipe]
cd ~/aipipe
git init -q -b main
git add -A
git status --short | head -30      # revisa: no debe haber __pycache__, claves ni archivos raros
git commit -qm "aipipe 0.5.1: importación inicial"
git remote add origin https://github.com/albertsp/aipipe.git
git push -u origin main
```

Comprueba en GitHub que el contenido es el esperado. Si el repositorio ya tiene historia, salta este paso.

## 4. El repositorio de trabajo y su configuración

```bash
# [VPS aipipe]
git clone https://github.com/albertsp/aipipe.git ~/work/aipipe
cd ~/work/aipipe
cat > .aipipe.toml <<'EOF'
[linear]
team = "ALB"

[project]
base_branch = "main"
test_command = "python -m pytest -q"
push = true
pr = true
EOF
set -a; source ~/.config/aipipe/env; set +a
aipipe doctor
aipipe run --dry-run
```

- **`.aipipe.toml` no se versiona** (está en `.gitignore`): así un PR no puede cambiar el sandbox, el equipo de Linear ni
  los comandos de prueba. Vive solo en el servidor.
- **`test_command`** se ejecuta dentro del sandbox, con el `PATH` del venv (donde está `pytest`). Si tu proyecto necesita
  otra cosa, [03-configuracion.md](03-configuracion.md).
- `doctor` debe mostrar el sandbox activo, `gh` disponible y `push`/`pr` activos. Ya puedes borrar el directorio de
  importación: `rm -rf ~/aipipe` (el código instalado vive en `~/venv`).

## 5. Primer ticket con PR

1. En Linear, crea un ticket **pequeño** (plantilla en la [guía de uso](01-guia-de-uso.md)), ponle `ai-ready`, y
   `ai:approve` si quieres ver el plan antes.
2. En el VPS: `aipipe run` (con la clave cargada).
3. Resultado esperado: el resumen muestra la URL del PR (`done … PR https://github.com/…/pull/1`), el ticket queda en
   *In Review* con el enlace, y en GitHub aparece la rama `ai/alb-…`.
4. **Revisa el PR** en GitHub (pestaña *Files changed*), con la tabla de «qué mirar» de abajo, y fusiónalo o pide cambios.

Si algo falla: [07-solucion-de-problemas.md §8](07-solucion-de-problemas.md#8-resultado-ramas-worktrees-git-y-github).

## 6. Dejarlo corriendo solo (servicio systemd)

La plantilla `deploy/aipipe-watch@.service` está **sin probar**: haz primero el paso 5 a mano y activa el servicio solo
cuando funcione.

```bash
# [VPS albert]
sudo cp /home/aipipe/work/aipipe/deploy/aipipe-watch@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now aipipe-watch@aipipe          # «aipipe» = ~aipipe/work/aipipe, el repositorio vigilado
systemctl status aipipe-watch@aipipe
journalctl -u aipipe-watch@aipipe -f                     # registro en vivo (Ctrl+C para salir)
```

Una instancia vigila **un** repositorio (el nombre tras `@` es la carpeta de `~/work`). Para otro repositorio, otra instancia
(`aipipe-watch@otro-repo`), con `max_concurrent = 1` para que no se pisen ([03](03-configuracion.md)). Un ticket
nuevo con `ai-ready` se recoge en ≤ 5 minutos (`--interval 300`); el tiempo por ticket depende de su tamaño.

**Limitación actual:** aipipe filtra Linear por equipo, no por proyecto. Con dos repositorios vigilados, ambos verían los
mismos tickets (ver [01-guia-de-uso.md](01-guia-de-uso.md)). Empieza con un repositorio.

## 7. Qué mirar al revisar un PR de aipipe

El diff lo ha escrito un modelo a partir de texto, y los tests los ha escrito también él. **Que pasen los tests no
basta.** Lee el diff completo, y pon atención extra en:

| Si el PR toca… | Por qué importa |
|---|---|
| `src/aipipe/sandbox.py`, `config.py` (sección `[sandbox]`), `opencode.py` | Es lo que aísla al agente de las claves. Un cambio aquí puede abrir la puerta sin que fallen los tests normales (los tests de seguridad se saltan dentro del sandbox) |
| `linear.py` (filtro de creador, aprobación) | Quién puede mandar ejecutar código |
| `pipeline.py`, `gitops.py` | Qué se sube y cuándo |
| `templates/agents/*`, `agents.py` | Los permisos de cada agente |
| `.github/`, `deploy/`, `pyproject.toml`, dependencias nuevas | Ejecución de código fuera del flujo normal |
| Cualquier URL, `curl`, `subprocess`, lectura de `~/.config` o variables de entorno | Posible fuga |

**Regla práctica:** los PR que toquen la primera fila **se revisan en el PC, con calma**, y antes de fusionarlos se pasan
los tests completos fuera del sandbox:

```bash
# [VPS aipipe]
cd ~/work/aipipe && git fetch origin && git switch --detach origin/ai/alb-NN-…   # la rama del PR
python -m pytest -q          # fuera del sandbox: aquí SÍ corren los tests de seguridad
git switch main
```

Y, si el ticket es de esa zona, ponle `ai:approve` para leer el plan antes de que se escriba una línea.

## 8. Mantener aipipe actualizado

Cuando fusionas un PR en `main`, el aipipe **instalado** no cambia solo: eso es deliberado (un fallo de un PR no debe
romper el runner a mitad de un ticket). Se actualiza con un comando, que **solo instala si pasan todos los tests**:

```bash
# [VPS aipipe]
bash ~/work/aipipe/deploy/update.sh
```

```bash
# [VPS albert]  (aipipe no tiene sudo; con la cola vacía o sin tickets en curso)
sudo systemctl restart aipipe-watch@aipipe
```

El script comprueba que estás en `main` y sin cambios locales, trae `origin/main`, pasa `pytest` con el código nuevo y solo
entonces hace `pip install`. Si los tests fallan, el aipipe instalado no se toca.

**Los demás repositorios** (los que aipipe desarrolla, no aipipe mismo) siempre parten del `origin/main` más reciente: no
hay que hacer nada para que «se vayan actualizando».

## 9. Qué sigue sin estar cubierto

- **Fusión automática:** no existe. Si algún día se añade, será solo para cambios de bajo riesgo y con los tests de
  seguridad en una integración continua fuera del servidor.
- **Integración continua:** los tests de seguridad no pueden correr dentro del sandbox (necesitan bubblewrap). Un flujo de
  GitHub Actions que los ejecute en cada PR sería un buen siguiente paso (añadir `.github/workflows` requiere permiso
  `workflow`, que el token **no** debe tener: se añade a mano desde la web).
- **Seguridad pendiente:** [05-seguridad.md](05-seguridad.md) (ALB-28 a 31). Con repositorios privados y sin secretos, el
  riesgo restante es código malicioso en un PR (por eso lo revisas) y filtración de lo que haya en el código por la red
  (por eso no debe haber secretos).
