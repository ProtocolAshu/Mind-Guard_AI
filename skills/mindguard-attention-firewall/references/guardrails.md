# Guardrails (hard constraints)

1. **Autonomy**: any action can be overridden in one tap; none is permanent.
2. **Essential apps exempt**: calls, messaging, maps/navigation, payments, banking, health, alarms, emergency and work apps are never delayed or blocked, whatever the risk.
3. **Privacy**: metadata only, on-device where possible, no content capture, no sharing without consent.
4. **Transparency**: every intervention carries a plain-language reason.
5. **No dark patterns**: no guilt, shaming, fake urgency or manipulative copy.
6. **Cooldown**: no new intervention within 10 minutes of the last one.
7. **Consent**: SOFT_BLOCK only if the user enabled it, and at most 2 per day (enforced; unknown count disables it).
8. **One rung at a time**: never more than one rung above the previous action.
9. **Fail soft**: unknown category, missing history or low confidence caps the action at NUDGE.
10. **Respect overrides**: 2+ overrides in 24h cap the action at NUDGE.
