package com.snothin.ghostsam.data.device

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import com.snothin.ghostsam.data.channel.ExploitChannel
import com.snothin.ghostsam.data.channel.SuController
import com.snothin.ghostsam.data.channel.isChannelFailure
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** The bundled BakaSU ksud is built from a non-main branch, so its compiled
 *  late-load default package is `com.resukisu.resukisu.dev`; the real manager is
 *  `com.resukisu.resukisu`. Late-load therefore detaches nothing and starts a
 *  package that is not installed, leaving the running manager on a stale fd
 *  opened before the module was loaded - so it reports "not installed" even
 *  though the kernel registered it.
 *
 *  Restarting it needs a way to stop another app. On the standalone route there
 *  is usually no su (the manager grants it, and the manager is the thing that is
 *  broken) and no debug bridge, so [ActivityManager.killBackgroundProcesses] is
 *  the last resort: KILL_BACKGROUND_PROCESSES is a normal permission and the
 *  manager is cached while GhostSam is in front. */
internal object ManagerRestart {

    /** [stoppedBy] is null when nothing could stop the manager process. */
    data class Outcome(
        val packageName: String,
        val stoppedBy: String?,
        val launched: Boolean,
    ) {
        val ok: Boolean get() = stoppedBy != null && launched
    }

    suspend fun restart(context: Context): Outcome = withContext(Dispatchers.IO) {
        val pkg = KernelSuProbe.preferredManagerPackage(context)
        if (pkg.isBlank()) return@withContext Outcome("", null, false)
        Outcome(pkg, stop(context, pkg), launch(context, pkg))
    }

    private suspend fun stop(context: Context, pkg: String): String? {
        val killedBackground = runCatching {
            val am = context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
            am.killBackgroundProcesses(pkg)
        }.isSuccess

        if (runCatching { SuController.isAvailable() }.getOrDefault(false)) {
            val output = runCatching { SuController.execCommand("am force-stop $pkg") }
                .getOrDefault("")
            if (!isChannelFailure(output)) return "su"
        }

        // Shizuku / wireless-debugging shell: `am force-stop` is allowed for the
        // shell uid, so this still works when su was never granted.
        val shell = runCatching {
            ExploitChannel.exec(arrayOf("sh", "-c", "am force-stop $pkg")).waitFor() == 0
        }.getOrDefault(false)
        if (shell) return "shell"

        return if (killedBackground) "killBackground" else null
    }

    private fun launch(context: Context, pkg: String): Boolean {
        val intent = runCatching {
            context.packageManager.getLaunchIntentForPackage(pkg)
        }.getOrNull() ?: return false
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return runCatching { context.startActivity(intent) }.isSuccess
    }
}
