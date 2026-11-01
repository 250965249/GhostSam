package com.snothin.ghostsam.data.prefs

import android.content.Context
import java.util.Properties

/** Human-readable version of the bundled ksud payload for each KSU variant.
 *  The values live in assets/payloads/ksud/versions.properties, which
 *  .github/workflows/auto-build.yml regenerates with
 *  tools/gen_payload_versions.py from the version stamp inside each payload
 *  binary. It is never edited by hand, so the label cannot drift from the
 *  payload. The fallbacks deliberately carry no version number: claiming a
 *  stale one is exactly the bug this manifest exists to prevent. */
internal object PayloadVersions {
    private val fallback: Map<KsudVariant, String> = mapOf(
        KsudVariant.Default to "KernelSU (bundled)",
        KsudVariant.Next to "KernelSU-Next (bundled)",
        KsudVariant.SukiSU to "SukiSU-Ultra (bundled)",
        KsudVariant.BakaSU to "BakaSU (bundled)",
    )

    fun label(context: Context, variant: KsudVariant): String = runCatching {
        val props = Properties()
        context.assets.open("payloads/ksud/versions.properties").use { props.load(it) }
        props.getProperty(variant.name.lowercase())?.trim()?.takeIf { it.isNotEmpty() }
            ?: fallback.getValue(variant)
    }.getOrElse { fallback.getValue(variant) }
}
