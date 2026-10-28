package com.snothin.ghostsam.data.prefs

import android.content.Context
import java.util.Properties

/** Human-readable version of the bundled ksud payload for each KSU variant.
 *  The values live in assets/payloads/ksud/versions.properties so a release can
 *  update them without touching the UI; the fallbacks keep the app usable when
 *  a locally overridden payload has no manifest. */
internal object PayloadVersions {
    private val fallback: Map<KsudVariant, String> = mapOf(
        KsudVariant.Default to "KernelSU v3.3.1 (32680)",
        KsudVariant.Next to "KernelSU-Next v3.4.2 (33355)",
        KsudVariant.SukiSU to "SukiSU-Ultra v4.2.1-r2(41098-6)",
        KsudVariant.BakaSU to "BakaSU v4.2.0-rc3 (35258)",
    )

    fun label(context: Context, variant: KsudVariant): String = runCatching {
        val props = Properties()
        context.assets.open("payloads/ksud/versions.properties").use { props.load(it) }
        props.getProperty(variant.name.lowercase())?.trim()?.takeIf { it.isNotEmpty() }
            ?: fallback.getValue(variant)
    }.getOrElse { fallback.getValue(variant) }
}
