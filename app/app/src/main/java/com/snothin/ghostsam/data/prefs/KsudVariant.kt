package com.snothin.ghostsam.data.prefs

/** KSU kernel-side variant (standalone route only): the chain re-delivers ksud every run; an
 *  already loaded variant is not replaced - switching needs a reboot. Effective value resolves
 *  at a single point (SettingsPrefs.effectiveKsudVariant). Key: ksud_variant. */
enum class KsudVariant {
    Default,
    Next,
    SukiSU,

    /** Fourth variant: MapleSU. Appended last on purpose - the stored pref and the
     *  settings dropdown index by ordinal, so existing installs keep their selection. */
    MapleSU;

    val label: String
        get() = when (this) {
            Default -> "KernelSU"
            Next -> "KernelSU-Next"
            SukiSU -> "SukiSU-Nova"
            MapleSU -> "MapleSU"
        }

    companion object {
        fun parse(raw: String?): KsudVariant =
            entries.firstOrNull { it.name.equals(raw, ignoreCase = true) } ?: Default
    }
}
