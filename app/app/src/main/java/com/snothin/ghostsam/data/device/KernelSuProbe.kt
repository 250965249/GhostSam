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

    private const val PACKAGE_SUKISU = "com.sukisu.ultra"

    private const val PACKAGE_BAKASU = "com.resukisu.resukisu"

    /** KernelSU-Next manager signing-cert SHA-256 (= kernel Kbuild KSU_NEXT_MANAGER_HASH): normal and
     *  -spoofed share the cert while spoofed names are random - the cert is the only stable identifier. */
    private const val KSU_NEXT_MANAGER_CERT_SHA256 =
        "79e590113c4c4c0c222978e413a5faa801666957b1212a328e46c00c69821bf7"

    /** SukiSU-Ultra manager signing-cert SHA-256 (= kernel Kbuild KSU_EXPECTED_HASH). */
    private const val SUKISU_MANAGER_CERT_SHA256 =
        "947ae944f3de4ed4c21a7e4f7953ecf351bfa2b36239da37a34111ad29993eef"

    /** BakaSU manager signing-cert SHA-256 (GhostSam test key used for the bundled manager). */
    private const val BAKASU_MANAGER_CERT_SHA256 =
        "8113db5e7d0b0de1a4635236a477083c85aeb94088595eee4c89705cf4cdab23"

    @Volatile
    private var ksuNextCertPackages: List<String> = emptyList()

    @Volatile
    private var sukisuCertPackages: List<String> = emptyList()

    @Volatile
    private var bakasuCertPackages: List<String> = emptyList()

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
    private const val KEY_PACKAGES_BAKASU = "packages_bakasu"
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
            prefs.getString(KEY_PACKAGES_BAKASU, null)?.let { raw ->
                val cached = raw.split(',').filter { it.isNotBlank() }
                if (cached.isNotEmpty()) {
                    bakasuCertPackages = (cached + bakasuCertPackages).distinct()
                        .sortedBy { if (it == PACKAGE_BAKASU) 0 else 1 }
                }
            }
        }
    }

    private fun persistCache(context: Context) {
        runCatching {
            context.getSharedPreferences(CACHE_PREFS, Context.MODE_PRIVATE).edit()
                .putString(KEY_PACKAGES_NEXT, ksuNextCertPackages.joinToString(","))
                .putString(KEY_PACKAGES_SUKISU, sukisuCertPackages.joinToString(","))
                .putString(KEY_PACKAGES_BAKASU, bakasuCertPackages.joinToString(","))
                .apply()
        }
    }

    private fun primaryPackage(variant: KsudVariant): String = when (variant) {
        KsudVariant.Default -> PACKAGE_KERNELSU
        KsudVariant.Next -> PACKAGE_KERNELSU_NEXT
        KsudVariant.SukiSU -> PACKAGE_SUKISU
        KsudVariant.BakaSU -> PACKAGE_BAKASU
    }

    private fun certHash(variant: KsudVariant): String? = when (variant) {
        KsudVariant.Next -> KSU_NEXT_MANAGER_CERT_SHA256
        KsudVariant.SukiSU -> SUKISU_MANAGER_CERT_SHA256
        KsudVariant.BakaSU -> BAKASU_MANAGER_CERT_SHA256
        KsudVariant.Default -> null
    }

    private fun currentPackages(variant: KsudVariant): List<String> = when (variant) {
        KsudVariant.Next -> ksuNextCertPackages
        KsudVariant.SukiSU -> sukisuCertPackages
        KsudVariant.BakaSU -> bakasuCertPackages
        KsudVariant.Default -> emptyList()
    }

    private fun setPackages(variant: KsudVariant, packages: List<String>) {
        when (variant) {
            KsudVariant.Next -> ksuNextCertPackages = packages
            KsudVariant.SukiSU -> sukisuCertPackages = packages
            KsudVariant.BakaSU -> bakasuCertPackages = packages
            KsudVariant.Default -> Unit
        }
    }

    private fun refreshCertPackages(context: Context) {
        val variant = SettingsPrefs.effectiveKsudVariant(context)
        val expectedHash = certHash(variant) ?: return
        loadCache(context)
        if (managerPackageCandidates(context).any { launchIntentVisible(context, it) }) return
        val now = android.os.SystemClock.elapsedRealtime()
        if (now - certScanAt < CERT_SCAN_TTL_MS) return
        certScanAt = now
        runCatching {
            val started = android.os.SystemClock.elapsedRealtime()
            val pm = context.packageManager
            val resolved = pm.queryIntentActivities(
                Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER),
                0,
            )
            val packages = resolved.map { it.activityInfo.packageName }.distinct()
            val spoofCandidates = packages.filter { SPOOFED_PACKAGE_RE.matches(it) }
            var discovered = certMatches(pm, spoofCandidates, expectedHash)
            var fullRan = false
            if (discovered.isEmpty() && now - fullScanAt >= FULL_SCAN_TTL_MS) {
                fullScanAt = now
                fullRan = true
                discovered = certMatches(pm, packages, expectedHash)
            }
            if (discovered.isNotEmpty()) {
                val merged = (discovered + currentPackages(variant)).distinct()
                    .sortedBy { if (it == primaryPackage(variant)) 0 else 1 }
                setPackages(variant, merged)
                persistCache(context)
            } else if (fullRan) {
                setPackages(variant, emptyList())
                persistCache(context)
            }
            Log.i(
                LOG_TAG,
                "ksu manager scan: variant=$variant launcher=${packages.size} " +
                    "prefilter=${spoofCandidates.size} full=$fullRan found=${discovered.size} " +
                    "ms=${android.os.SystemClock.elapsedRealtime() - started}",
            )
        }
    }

    private fun certMatches(
        pm: PackageManager,
        packages: List<String>,
        expectedHash: String,
    ): List<String> {
        if (packages.isEmpty()) return emptyList()
        val pool = Executors.newFixedThreadPool(minOf(4, packages.size))
        return try {
            pool.invokeAll(
                packages.map { pkg -> Callable<String?> { certMatched(pm, pkg, expectedHash) } },
            ).mapNotNull { runCatching { it.get() }.getOrNull() }
        } finally {
            pool.shutdown()
        }
    }

    private fun certMatched(pm: PackageManager, pkg: String, expectedHash: String): String? {
        val info = runCatching {
            pm.getPackageInfo(
                pkg,
                PackageManager.PackageInfoFlags.of(
                    PackageManager.GET_SIGNING_CERTIFICATES.toLong(),
                ),
            )
        }.getOrNull() ?: return null
        val signers = info.signingInfo?.apkContentsSigners ?: return null
        val matched = signers.any { signer ->
            MessageDigest.getInstance("SHA-256")
                .digest(signer.toByteArray())
                .toSha256Hex() == expectedHash
        }
        return if (matched) pkg else null
    }

    private fun ByteArray.toSha256Hex(): String =
        joinToString("") { "%02x".format(it.toInt() and 0xFF) }

    /** Manager package candidates strictly by effective variant (uapi is not interchangeable;
     *  a cross-variant fallback lands the user on a manager for another kernel). */
    fun managerPackageCandidates(context: Context): List<String> {
        loadCache(context)
        return when (SettingsPrefs.effectiveKsudVariant(context)) {
            KsudVariant.Default -> listOf(PACKAGE_KERNELSU)
            KsudVariant.Next ->
                if (PACKAGE_KERNELSU_NEXT in ksuNextCertPackages) ksuNextCertPackages
                else ksuNextCertPackages + PACKAGE_KERNELSU_NEXT
            KsudVariant.SukiSU ->
                if (PACKAGE_SUKISU in sukisuCertPackages) sukisuCertPackages
                else sukisuCertPackages + PACKAGE_SUKISU
            KsudVariant.BakaSU ->
                if (PACKAGE_BAKASU in bakasuCertPackages) bakasuCertPackages
                else bakasuCertPackages + PACKAGE_BAKASU
        }
    }

    fun preferredManagerPackage(context: Context): String {
        val candidates = managerPackageCandidates(context)
        return candidates.firstOrNull { launchIntentVisible(context, it) } ?: candidates.first()
    }

    /** Rooted but su denied: rooted = this-boot receipt or module fallback; denied = SuController.isAvailable
     *  false. A no-ksu session has no su by design (unless upgraded - then expect a pending grant). */
    fun isRootedWithSuDenied(context: Context): Boolean {
        if (SuController.isAvailable()) return false
        if (ExploitPrefs.receiptMode(context) == "noksu" && !ExploitPrefs.isKsudUpgraded(context)) return false
        val token = ExploitPrefs.currentBootToken()
        return (token != null && ExploitPrefs.isReceiptVerified(context, token)) || isActive()
    }

    fun isManagerInstalled(context: Context): Boolean =
        managerPackageCandidates(context).any { launchIntentVisible(context, it) }

    fun probeManagerInstalled(context: Context): Boolean {
        if (isManagerInstalled(context)) return true
        refreshCertPackages(context)
        return isManagerInstalled(context)
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
