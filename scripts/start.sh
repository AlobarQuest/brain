#!/bin/sh
set -e
: "${BRAIN_TYPE:?BRAIN_TYPE is required}"
BRAIN_DIR="src/brains/${BRAIN_TYPE}"
[ -d "$BRAIN_DIR" ] || { echo "unknown BRAIN_TYPE: $BRAIN_TYPE" >&2; exit 1; }

alembic -c "${BRAIN_DIR}/alembic.ini" upgrade head

if [ -f "${BRAIN_DIR}/seed.py" ]; then
  python -m "src.brains.${BRAIN_TYPE}.seed" --skip-existing
fi

exec uvicorn src.core.app:create_app --factory --host 0.0.0.0 --port "${PORT:-8000}"
