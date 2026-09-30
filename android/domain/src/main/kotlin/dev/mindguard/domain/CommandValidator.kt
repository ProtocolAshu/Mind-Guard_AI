package dev.mindguard.domain

sealed interface CommandCheck {
    data class Execute(val command: DeviceCommand) : CommandCheck
    data class Reject(val reason: String) : CommandCheck
}

/**
 * Defence in depth on the device: even an authorized command is refused if it expired, targets an essential app,
 * exceeds platform duration caps, or is a restriction without the user's escape hatches (Allow once / Emergency).
 */
object CommandValidator {
    private val maxSeconds = mapOf(
        CommandName.DELAY_GATE to 10 * 60, CommandName.LIMIT_ACCESS to 30 * 60, CommandName.BLOCK_APP to 120 * 60, CommandName.START_FOCUS to 180 * 60,
    )
    private val restrictive = setOf(CommandName.DELAY_GATE, CommandName.LIMIT_ACCESS, CommandName.BLOCK_APP)

    fun check(command: DeviceCommand, nowMs: Long): CommandCheck {
        if (command.command == CommandName.NONE) return CommandCheck.Reject("no action")
        command.expiresAtEpochMs?.let { if (it <= nowMs) return CommandCheck.Reject("expired") }
        if (command.appPackage != null && !AppCatalog.isValidPackage(command.appPackage)) return CommandCheck.Reject("invalid package")
        if (AppCatalog.isEssential(command.appPackage)) return CommandCheck.Reject("essential app")
        if (command.durationSeconds < 0) return CommandCheck.Reject("negative duration")
        maxSeconds[command.command]?.let { cap -> if (command.durationSeconds > cap) return CommandCheck.Reject("duration above cap") }
        if (command.command in restrictive) {
            if (command.interventionId == null) return CommandCheck.Reject("restriction without intervention id")
            if (!command.reversible || CommandAction.ALLOW_ONCE !in command.actions || CommandAction.EMERGENCY !in command.actions) {
                return CommandCheck.Reject("restriction without user override actions")
            }
        }
        return CommandCheck.Execute(command.copy(title = command.title.take(80), body = command.body.take(400)))
    }
}
