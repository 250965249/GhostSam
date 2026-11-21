package com.snothin.ghostsam.data.device

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
 *  package that is not installed, leaving the running manager on a stale fd.
 *  Restart the effective variant's real package so it re-opens the kernel fd. */
internal object ManagerRestart {

    /** Force-stop (root, else the privileged shell channel) and relaunch.
     *  Relaunch needs no privilege: a launcher activity is exported. */
    suspend fun restart(context: Context): Boolean = withContext(Dispatchers.IO) {
        val pkg = KernelSuProbe.preferredManagerPackage(context)
        if (pkg.isBlank()) return@withContext false
        val stopped = stop(pkg)
        val opened = openManager(context, pkg)
        stopped && opened
    }

    private suspend fun stop(pkg: String): Boolean {
        if (SuController.isAvailable()) {
            val output = runCatching { SuController.execCommand("am force-stop $pkg") }
                .getOrDefault("")
            if (!isChannelFailure(output)) return true
        }
        // Shizuku / wireless-debugging shell: `am force-stop` is allowed for the
        // shell uid, so this still works when su was never granted.
        return runCatching {
            val process = ExploitChannel.exec(arrayOf("sh", "-c", "am force-stop $pkg"))
            process.waitFor() == 0
        }.getOrDefault(false)
    }

    private fun openManager(context: Context, pkg: String): Boolean {
        val intent = runCatching {
            context.packageManager.getLaunchIntentForPackage(pkg)
        }.getOrNull() ?: return false
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return runCatching { context.startActivity(intent) }.isSuccess
    }
}
