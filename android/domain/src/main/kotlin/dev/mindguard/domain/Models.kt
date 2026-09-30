package dev.mindguard.domain

/** Wire vocabulary shared with the backend (backend/app/schemas/common.py). */
enum class InterventionType { ALLOW, SOFT_WARNING, MINDFUL_PROMPT, DELAY, LIMITED_ACCESS, TEMPORARY_BLOCK, FOCUS_MODE, REQUEST_CONFIRMATION }

enum class OutcomeType(val wire: String) {
    ACCEPTED("accepted"), OVERRIDDEN("overridden"), IGNORED("ignored"), STOPPED_SESSION("stopped_session"),
    CONTINUED_SESSION("continued_session"), RETURNED_TO_TASK("returned_to_task"), DISABLED_PROTECTION("disabled_protection"),
}

enum class CommandName { NONE, NOTIFY, MINDFUL_PROMPT, CONFIRM, DELAY_GATE, LIMIT_ACCESS, BLOCK_APP, START_FOCUS }

enum class CommandAction { ACCEPT, CONTINUE, STOP, ALLOW_ONCE, EMERGENCY, START_FOCUS }

enum class OverrideKind(val wire: String) {
    ALLOW_ONCE("allow_once"), PAUSE("pause"), DISABLE_30M("disable_30m"), DISABLE_UNTIL_TOMORROW("disable_until_tomorrow"), EMERGENCY("emergency"),
}

enum class AppCategory(val wire: String) {
    SOCIAL_MEDIA("social_media"), VIDEO("video"), MESSAGING("messaging"), BROWSER("browser"), GAMES("games"),
    PRODUCTIVITY("productivity"), EDUCATION("education"), NEWS("news"), ESSENTIAL("essential"), OTHER("other");

    companion object {
        fun fromWire(value: String?): AppCategory = entries.firstOrNull { it.wire == value } ?: OTHER
    }
}

/** A command the backend authorized after its guardrail engine. The device validates it again before acting. */
data class DeviceCommand(
    val command: CommandName,
    val interventionId: String?,
    val appPackage: String?,
    val durationSeconds: Int,
    val title: String,
    val body: String,
    val actions: List<CommandAction>,
    val expiresAtEpochMs: Long?,
    val reversible: Boolean,
)

/** Client event in the exact POST /api/events schema. */
data class ClientEvent(
    val clientEventId: String,
    val eventType: String,
    val occurredAtEpochMs: Long,
    val appPackage: String?,
    val payload: Map<String, Any?> = emptyMap(),
)

data class DeviceCapabilities(val usageAccess: Boolean, val notifications: Boolean, val overlay: Boolean, val dndAccess: Boolean)
