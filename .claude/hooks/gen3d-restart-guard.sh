#!/usr/bin/env bash
# PreToolUse(Bash) guard: refuse to restart the gen3d service while a print is
# running on groundskeeper (a restart tears down the print thread and kills it).
# Fast path: only touches the network when the command actually restarts gen3d.
input=$(cat)
printf '%s' "$input" | grep -q "restart gen3d" || exit 0
st=$(ssh -o ConnectTimeout=5 pi@groundskeeper "curl -s http://localhost:5000/status" 2>/dev/null)
if printf '%s' "$st" | grep -q '"printing": true'; then
  printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"A print is running on groundskeeper (:5000 reports printing:true). Restarting gen3d would kill it. Wait for the print to finish, or stop it first, then retry."}}'
fi
exit 0
