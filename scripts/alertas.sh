#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.."
DC="docker compose -f docker-compose.secure.yml"
VENTANA="${VENTANA:-10m}"
UMBRAL_LOGIN="${UMBRAL_LOGIN:-5}"

LOGS=$($DC logs --no-log-prefix --since "$VENTANA" fastapi-app 2>/dev/null)
fallos=$(grep -c '"event": "login_failed"' <<<"$LOGS")
bloqueos=$(grep -c '"event": "login_blocked"' <<<"$LOGS")
rechazos=$(grep -c '"event": "input_rejected"' <<<"$LOGS")
picos=$(grep -c '"event": "search_spike"' <<<"$LOGS")

echo "Ventana: últimos $VENTANA"
printf '  login_failed=%s  login_blocked=%s  input_rejected=%s  search_spike=%s\n' \
  "$fallos" "$bloqueos" "$rechazos" "$picos"

(( fallos >= UMBRAL_LOGIN )) && echo "ALERTA [AUTH]   $fallos logins fallidos (umbral $UMBRAL_LOGIN) -> posible fuerza bruta"
(( bloqueos > 0 ))          && echo "ALERTA [AUTH]   $bloqueos peticiones bloqueadas por rate-limit"
(( rechazos > 0 ))          && echo "ALERTA [INPUT]  $rechazos payloads rechazados por validación -> posible inyección"
(( picos > 0 ))             && echo "ALERTA [SEARCH] pico de búsquedas detectado:" \
                            && grep '"event": "search_spike"' <<<"$LOGS" | tail -n 3
exit 0
