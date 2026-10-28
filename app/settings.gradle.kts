pluginManagement {
    repositories {
        // Mainland-China mirror for Google Maven (dl.google.com is often unreachable there).
        maven("https://maven.aliyun.com/repository/google")
        maven("https://maven.aliyun.com/repository/public")
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        maven("https://maven.aliyun.com/repository/google")
        maven("https://maven.aliyun.com/repository/public")
        google()
        mavenCentral()
        // libadb-android (from App Manager; published on JitPack only)
        maven("https://jitpack.io")
    }
}

rootProject.name = "GhostSam"
include(":app")
