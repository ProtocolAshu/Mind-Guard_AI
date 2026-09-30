package dev.mindguard.domain

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class CommandValidatorTest {
    private val now = 1_000_000L
    private fun block(pkg: String = "com.instagram.android", seconds: Int = 900, actions: List<CommandAction> = listOf(CommandAction.ACCEPT, CommandAction.ALLOW_ONCE, CommandAction.EMERGENCY)) =
        DeviceCommand(CommandName.BLOCK_APP, "iv-1", pkg, seconds, "Instagram is paused", "because…", actions, now + 60_000, reversible = true)

    @Test
    fun acceptsAValidReversibleRestriction() {
        assertTrue(CommandValidator.check(block(), now) is CommandCheck.Execute)
    }

    @Test
    fun rejectsExpiredEssentialOversizedAndIrreversibleCommands() {
        assertEquals("expired", (CommandValidator.check(block().copy(expiresAtEpochMs = now - 1), now) as CommandCheck.Reject).reason)
        assertEquals("essential app", (CommandValidator.check(block(pkg = "com.google.android.dialer"), now) as CommandCheck.Reject).reason)
        assertEquals("duration above cap", (CommandValidator.check(block(seconds = 3 * 3600), now) as CommandCheck.Reject).reason)
        assertEquals("restriction without user override actions",
            (CommandValidator.check(block(actions = listOf(CommandAction.ACCEPT)), now) as CommandCheck.Reject).reason)
        assertEquals("invalid package", (CommandValidator.check(block(pkg = "../../etc"), now) as CommandCheck.Reject).reason)
    }

    @Test
    fun softCommandsNeedNoOverrideActionsAndTextIsBounded() {
        val notify = DeviceCommand(CommandName.NOTIFY, "iv-2", "com.instagram.android", 0, "x".repeat(200), "y".repeat(900), listOf(CommandAction.ACCEPT), null, true)
        val checked = CommandValidator.check(notify, now) as CommandCheck.Execute
        assertEquals(80, checked.command.title.length)
        assertEquals(400, checked.command.body.length)
    }
}
