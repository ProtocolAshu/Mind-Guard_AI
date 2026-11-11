# Decision framework

## Signals
| Signal | Meaning | Missing means |
|---|---|---|
| session_min | continuous minutes in the app | lower confidence |
| scroll_per_min | scroll/swipe rate | lower confidence |
| hour | local hour 0-23 | lower confidence |
| streak_days | days over the user's limit | lower confidence |
| category | social, video, gaming, news, browser, other_leisure, or an essential type | unknown: capped at NUDGE |
| current_action | previous action in the ladder | unknown: capped at NUDGE |
| mins_since_last_intervention | cooldown clock | unknown: capped at NUDGE |
| recent_overrides | overrides in last 24h | treated as 0 |
| user_limit_min | user-defined session limit (positive number) | optional; if missing or invalid, over-limit term is 0 |
| softblock_enabled, softblocks_today | consent and daily count | unknown: SOFT_BLOCK disabled |
| preferred_action | host's learned policy suggestion | rule-based action used |

Invalid values (wrong type, negative, non-finite, hour outside 0-23, non-integer counts) count as unknown and cap the action at NUDGE. `softblock_enabled` must be Boolean `true`; strings and numbers do not count as consent.

## Risk score
`risk = 0.35*session + 0.25*scroll + 0.15*night + 0.15*streak + 0.10*over_limit`, each term normalised to [0,1]. Confidence is the share of the four core signals present; below 0.5 the cap is NUDGE.

## Risk to ladder (before guardrails)
under 0.25 ALLOW; 0.25-0.45 NUDGE; 0.45-0.65 DELAY; 0.65-0.85 FRICTION; 0.85 and above SOFT_BLOCK.

## Learning and logging (host responsibility)
This script is stateless. The host app should log timestamp, signal summary (no content), risk, allowed set, action, reason and outcome, let users view and delete the log, and update any learned policy from outcomes (+1 accepted, 0 ignored, -1 override).
