#!/usr/bin/env bash
# Actualiza el aipipe INSTALADO desde la rama main de GitHub. Se ejecuta como el usuario `aipipe`.
#
#   ~/work/aipipe/deploy/update.sh
#
# Orden de seguridad: 1) trae main, 2) pasa TODOS los tests (fuera del sandbox, con el codigo nuevo), 3) solo entonces
# instala. Si los tests fallan, el aipipe instalado no se toca. No reinicia el servicio (aipipe no tiene sudo):
#   sudo systemctl restart aipipe-watch@<repo>      (como albert; con la cola vacia o sin tickets en curso)
#
# Variables: AIPIPE_REPO (por defecto ~/work/aipipe), AIPIPE_VENV (por defecto ~/venv).
set -euo pipefail

repo="${AIPIPE_REPO:-$HOME/work/aipipe}"
venv="${AIPIPE_VENV:-$HOME/venv}"

cd "$repo"
branch="$(git rev-parse --abbrev-ref HEAD)"
if [ "$branch" != "main" ]; then
  echo "ERROR: $repo esta en la rama '$branch', no en main. Cambia a main (git switch main) y repite." >&2
  exit 1
fi
if [ -n "$(git status --porcelain)" ]; then
  echo "ERROR: hay cambios sin commitear en $repo; no actualizo." >&2
  git status --short >&2
  exit 1
fi

git fetch -q origin main
if [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ]; then
  echo "Ya estas en la ultima version: $("$venv/bin/aipipe" --version)"
  exit 0
fi

echo "Cambios que entran:"
git --no-pager log --oneline HEAD..origin/main
git merge --ff-only -q origin/main

echo "Pasando los tests con el codigo nuevo..."
if ! "$venv/bin/python" -m pytest -q; then
  echo "ERROR: los tests fallan con el codigo nuevo. NO se instala; el aipipe instalado sigue como estaba." >&2
  echo "       (el checkout $repo ya esta en la version nueva; para volver: git reset --hard ORIG_HEAD)" >&2
  exit 1
fi

"$venv/bin/pip" install -q --upgrade --force-reinstall "$repo"
echo "Instalado: $("$venv/bin/aipipe" --version)"

echo "Reinstalando los agentes de OpenCode..."
if ! "$venv/bin/aipipe" install-agents --force; then
  echo "ERROR: no se pudieron reinstalar los agentes. Revisa la salida de arriba y repite con: aipipe install-agents --force" >&2
  exit 1
fi

echo "Estado tras la actualizacion (solo avisos y errores de aipipe doctor):"
"$venv/bin/aipipe" doctor | grep -E '^\[(ERR|-- )' || true

echo "Falta reiniciar el servicio (como albert):  sudo systemctl restart aipipe-watch@<repo>"
