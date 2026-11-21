package com.snothin.ghostsam.data.device

import android.content.Context
import com.snothin.ghostsam.data.channel.SuController

/** The bundled BakaSU ksud is built from a non-main branch, so its compiled
 *  late-load default package is `com.resukisu.resukisu.dev`; the real manager is
 *  `com.resukisu.resukisu`. Late-load therefore detaches the correct manager's
 *  old process without starting it again. Restart the effective variant's
 *  manager explicitly so it opens a fresh kernel fd. */
internal object ManagerRestart {
    suspend fun restart(context: Context): Boolean {
        val pkg = KernelSuProbe.preferredManagerPackage(context)
        if (pkg.isBlank()) return false
        val output = SuController.execCommand(
            "am force-stop $pkg; " +
                "am start -a android.intent.action.MAIN " +
                "-c android.intent.category.LAUNCHER -p $pkg",
            timeoutMillis = 15_000,
        )
        return !output.contains("Error", ignoreCase = true) &&
            !output.contains("Exception", ignoreCase = true)
    }
}
