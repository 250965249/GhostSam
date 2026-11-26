package com.snothin.ghostsam.data.prefs

/** KSU kernel-side variant (standalone route only): the chain re-delivers ksud every run; an
 *  already loaded variant is not replaced - switching needs a reboot. Effective value resolves
 *  at a single point (SettingsPrefs.effectiveKsudVariant). Key: ksud_variant. */
enum class KsudVariant {
    Default,
    Next,
    SukiSU,
    MapleSU;

    val label: String
        get() = when (this) {
            Default -> "KernelSU"
            Next -> "KernelSU-Next"
            SukiSU -> "SukiSU-Nova"
            MapleSU -> "MapleSU"
        }

    companion object {
        const val PACKAGE_MAPLESU = "dev.maplesu.manager"
        const val MAPLESU_CERT_SHA256 =
            "cd0c477bcacc58c694367cb9a2f696d3bfd5de3e0ee8a0e25451cecbf312db90"
        const val MAPLESU_CERT_DER_SIZE = 911

        fun parse(raw: String?): KsudVariant =
            entries.firstOrNull { it.name.equals(raw, ignoreCase = true) } ?: Default
    }
}
