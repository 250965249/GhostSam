package com.snothin.ghostsam.data.device

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.util.Log
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.Callable
import java.util.concurrent.Executors
import com.snothin.ghostsam.data.channel.ExploitChannel
import com.snothin.ghostsam.data.channel.SuController
import com.snothin.ghostsam.data.prefs.ExploitPrefs
import com.snothin.ghostsam.data.prefs.KsudVariant
import com.snothin.ghostsam.data.prefs.SettingsPrefs

/** KernelSU activation probe. A Shizuku grant != root; on Samsung, file probes alone are unreliable
 *  (/sys/module hidden; app uid cannot read /proc/modules) - prefer the channel probe once ready. */
object KernelSuProbe {
    private const val PACKAGE_KERNELSU = "me.weishu.kernelsu"

    private const val PACKAGE_KERNELSU_NEXT = "com.rifsxd.ksunext"

    /** SukiSU-Ultra renamed to SukiSU-Nova; the app id followed (com.sukisu.nova). */
    private const val PACKAGE_SUKISU = "com.sukisu.nova"

    /** Pre-rename SukiSU manager, still recognised so an older install keeps working. */
    private const val PACKAGE_SUKISU_LEGACY = "com.sukisu.ultra"

    /** KernelSU-Next manager signing-cert SHA-256 (= kernel Kbuild KSU_NEXT_MANAGER_HASH): normal and
     *  -spoofed share the cert while spoofed names are random - the cert is the only stable identifier. */
    private const val KSU_NEXT_MANAGER_CERT_SHA256 =
        "79e590113c4c4c0c222978e413a5faa801666957b1212a328e46c00c69821bf7"

    /** SukiSU-Ultra manager signing-cert SHA-256 (= kernel Kbuild KSU_EXPECTED_HASH). */
    private const val SUKISU_MANAGER_CERT_SHA256 =
        "947ae944f3de4ed4c21a7e4f7953ecf351bfa2b36239da37a34111ad29993eef"

    /** MapleSU Normal manager package (applicationId). */
    private const val PACKAGE_MAPLESU = KsudVariant.PACKAGE_MAPLESU

    /** MapleSU Normal manager signing-cert SHA-256, taken from the MapleSU source manifest
     *  (identity.expectedHash, DER 772 B). NOT yet re-verified against a real signed APK on this
     *  host; keep it a strict equality check (never relaxed). */
    private const val MAPLESU_MANAGER_CERT_SHA256 = KsudVariant.MAPLESU_CERT_SHA256

    /** t38 fork test cert DER size; strict equality only (no relaxed branch). */
    private const val MAPLESU_MANAGER_CERT_DER_SIZE = 911

    @Volatile
    private var ksuNextCertPackages: List<String> = emptyList()

    @Volatile
    private var sukisuCertPackages: List<String> = emptyList()

    @Volatile
    private var mapleSuCertPackages: List<String> = emptyList()

    @Volatile
    private var certScanAt = 0L

    @Volatile
    private var fullScanAt = 0L

    @Volatile
    private var cacheLoaded = false

    private const val CERT_SCAN_TTL_MS = 60_000L

    private const val FULL_SCAN_TTL_MS = 5 * 60_000L

    private val SPOOFED_PACKAGE_RE = Regex("^[a-z]{6}\\.[a-z]{6}\\.[a-z]{6}$")

    private const val CACHE_PREFS = "ksu_manager_cache"
    private const val KEY_PACKAGES_NEXT = "packages_next"
    private const val KEY_PACKAGES_SUKISU = "packages_sukisu"
    private const val LOG_TAG = "GhostSam"

    private fun loadCache(context: Context) {
        if (cacheLoaded) return
        cacheLoaded = true
        runCatching {
            val prefs = context.getSharedPreferences(CACHE_PREFS, Context.MODE_PRIVATE)
            prefs.getString(KEY_PACKAGES_NEXT, null)?.let { raw ->
                val cached = raw.split(',').filter { it.isNotBlank() }
                if (cached.isNotEmpty()) {
                    ksuNextCertPackages = (cached + ksuNextCertPackages).distinct()
                        .sortedBy { if (it == PACKAGE_KERNELSU_NEXT) 0 else 1 }
                }
            }
            prefs.getString(KEY_PACKAGES_SUKISU, null)?.let { raw ->
                val cached = raw.split(',').filter { it.isNotBlank() }
                if (cached.isNotEmpty()) {
                    sukisuCertPackages = (cached + sukisuCertPackages).distinct()
                        .sortedBy { if (it == PACKAGE_SUKISU) 0 else 1 }
                }
            }
        }
    }

    private fun persistCache(context: Context) {
        runCatching {
            context.getSharedPreferences(CACHE_PREFS, Context.MODE_PRIVATE).edit()
                .putString(KEY_PACKAGES_NEXT, ksuNextCertPackages.joinToString(","))
                .putString(KEY_PACKAGES_SUKISU, sukisuCertPackages.joinToString(","))
                .apply()
        }
    }

    private fun primaryPackage(variant: KsudVariant): String = when (variant) {
        KsudVariant.Default -> PACKAGE_KERNELSU
        KsudVariant.Next -> PACKAGE_KERNELSU_NEXT
        KsudVariant.SukiSU -> PACKAGE_SUKISU
        KsudVariant.MapleSU -> PACKAGE_MAPLESU
    }

    private fun certHash(variant: KsudVariant): String? = when (variant) {
        KsudVariant.Next -> KSU_NEXT_MANAGER_CERT_SHA256
        KsudVariant.SukiSU -> SUKISU_MANAGER_CERT_SHA256
        KsudVariant.MapleSU -> MAPLESU_MANAGER_CERT_SHA256
        KsudVariant.Default -> null
    }

    fun certDerSize(variant: KsudVariant): Int = when (variant) {
        KsudVariant.MapleSU -> MAPLESU_MANAGER_CERT_DER_SIZE
        else -> 0
    }

    private fun currentPackages(variant: KsudVariant): List<String> = when (variant) {
        KsudVariant.Next -> ksuNextCertPackages
        KsudVariant.SukiSU -> sukisuCertPackages
        KsudVariant.MapleSU -> mapleSuCertPackages
        KsudVariant.Default -> emptyList()
    }

    private fun setPackages(variant: KsudVariant, packages: List<String>) {
        when (variant) {
            KsudVariant.Next -> ksuNextCertPackages = packages
            KsudVariant.SukiSU -> sukisuCertPackages = packages
            KsudVariant.MapleSU -> mapleSuCertPackages = packages
            KsudVariant.Default -> Unit
        }
    }

    fun preferredManagerPackage(context: Context): String {
        val candidates = managerPackageCandidates(context)
        return candidates.firstOrNull { managerUsable(context, it) } ?: candidates.first()
    }

    private fun managerUsable(context: Context, pkg: String): Boolean {
        if (!launchIntentVisible(context, pkg)) return false
        val expected = certHash(SettingsPrefs.effectiveKsudVariant(context)) ?: return true
        return certMatched(context.packageManager, pkg, expected) != null
    }

    /** Rooted but su denied: rooted = this-boot receipt or module fallback; denied = SuController.isAvailable
     *  false. A no-ksu session has no su by design (unless upgraded - then expect a pending grant). */
    fun isRootedWithSuDenied(context: Context): Boolean {
        if (SuController.isAvailable()) return false
        if (ExploitPrefs.receiptMode(context) == "noksu" && !ExploitPrefs.isKsudUpgraded(context)) return false
        val token = ExploitPrefs.currentBootToken()
        return (token != null && ExploitPrefs.isReceiptVerified(context, token)) || isActive()
    }

    /** A matching package name alone is insufficient: the kernel authenticates
     * the manager certificate as well. Check known and cached packages alike. */
    fun isManagerInstalled(context: Context): Boolean =
        managerPackageCandidates(context).any { managerUsable(context, it) }

    fun probeManagerInstalled(context: Context): Boolean {
        if (isManagerInstalled(context)) return true
        refreshCertPackages(context)
        return isManagerInstalled(context)
    }

    /** Package timestamps only establish a change during this boot, not whether
     * it happened before or after module loading. A changed manager may need a
     * reboot to refresh registration; never suggest loading the module twice. */
    fun managerChangedThisBoot(context: Context): Boolean {
        val bootWallClock = System.currentTimeMillis() - android.os.SystemClock.elapsedRealtime()
        return managerPackageCandidates(context).any { pkg ->
            runCatching {
                val info = context.packageManager.getPackageInfo(pkg, 0)
                maxOf(info.lastUpdateTime, info.firstInstallTime) > bootWallClock
            }.getOrDefault(false)
        }
    }

    private fun launchIntentVisible(context: Context, pkg: String): Boolean = runCatching {
        context.packageManager.getLaunchIntentForPackage(pkg) != null
    }.getOrDefault(false)

    fun isActive(): Boolean {
        if (File("/sys/module/kernelsu").exists()) return true
        return runCatching {
            File("/proc/modules").readText().lineSequence()
                .any { it.startsWith("kernelsu ") }
        }.getOrDefault(false)
    }

    fun isActiveViaShizuku(): Boolean = runCatching {
        val process = ExploitChannel.exec(
            arrayOf("sh", "-c", "grep -q '^kernelsu ' /proc/modules && su -c id"),
        )
        val output = readProcessBounded(process)
        process.waitFor() == 0 && output.contains("uid=0")
    }.getOrDefault(false)

    fun isModuleLoadedViaChannel(): Boolean {
        val now = android.os.SystemClock.elapsedRealtime()
        if (moduleProbeAt != 0L && now - moduleProbeAt < MODULE_PROBE_TTL_MS) return moduleProbeValue
        val result = runCatching {
            val process = ExploitChannel.exec(
                arrayOf("sh", "-c", "grep -q '^kernelsu ' /proc/modules && echo LOADED"),
            )
            val output = readProcessBounded(process)
            process.waitFor()
            output.contains("LOADED")
        }.getOrDefault(false)
        moduleProbeValue = result
        moduleProbeAt = now
        return result
    }

    private const val MODULE_PROBE_TTL_MS = 5_000L

    @Volatile
    private var moduleProbeValue: Boolean = false

    @Volatile
    private var moduleProbeAt: Long = 0L

    private fun readProcessBounded(process: Process, timeoutMs: Long = 5_000): String {
        val holder = arrayOf("")
        val reader = Thread {
            runCatching { holder[0] = process.inputStream.bufferedReader().use { it.readText() } }
        }.apply { isDaemon = true }
        reader.start()
        reader.join(timeoutMs)
        if (reader.isAlive) runCatching { process.destroy() }
        return holder[0]
    }

    enum class SuAuthState { Rooted, Denied, Unavailable }

    private const val SU_AUTH_PROBE =
        "grep -q '^kernelsu ' /proc/modules && { command -v su; su -c id; } || echo NO_MODULE"

    private const val SU_AUTH_NO_MODULE = "NO_MODULE"

    // Module check comes first: after a reboot su binaries may linger while the module is gone;
    // that is "needs re-exploit", NOT "not granted".
    fun suAuthState(): SuAuthState = runCatching {
        val process = ExploitChannel.exec(arrayOf("sh", "-c", SU_AUTH_PROBE))
        val output = readProcessBounded(process)
        process.waitFor()
        when {
            output.contains(SU_AUTH_NO_MODULE) -> SuAuthState.Unavailable
            output.contains("uid=0") -> SuAuthState.Rooted
            output.lineSequence().any { it.contains("su") } -> SuAuthState.Denied
            else -> SuAuthState.Unavailable
        }
    }.getOrDefault(SuAuthState.Unavailable)
}
