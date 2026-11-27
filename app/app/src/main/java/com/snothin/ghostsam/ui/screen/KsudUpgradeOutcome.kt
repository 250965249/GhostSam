package com.snothin.ghostsam.ui.screen

/**
 * Result of the ksud late-load confirmation probe.
 *
 * The previous code treated "module not probed + temp socket gone" as success
 * (`ok = true`), which misreported an unconfirmed upgrade as an initialized channel.
 * Map the observable inputs to distinct outcomes and only report INITIALIZED when the
 * module probe actually confirms an active module. The three ambiguity inputs
 * (probe-failed+connection-closed, timeout, version-only) must NOT read "初始化完成".
 */
internal enum class KsudUpgradeOutcome(val message: String, val initialized: Boolean) {
    INITIALIZED("初始化完成", true),
    CONNECTION_CLOSED_UNCONFIRMED("连接已关闭，结果未确认", false),
    TIMEOUT("超时", false),
    MODULE_RESPONSIVE("模块可响应", false),
}

internal fun classifyKsudUpgradeOutcome(
    moduleActive: Boolean,
    versionResponded: Boolean,
    timedOut: Boolean,
    channelClosed: Boolean,
): KsudUpgradeOutcome = when {
    moduleActive -> KsudUpgradeOutcome.INITIALIZED
    timedOut -> KsudUpgradeOutcome.TIMEOUT
    versionResponded -> KsudUpgradeOutcome.MODULE_RESPONSIVE
    channelClosed -> KsudUpgradeOutcome.CONNECTION_CLOSED_UNCONFIRMED
    else -> KsudUpgradeOutcome.MODULE_RESPONSIVE
}
