plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "dev.rymiwe.seats"
    compileSdk = 35

    defaultConfig {
        applicationId = "dev.rymiwe.seats"
        minSdk = 26
        targetSdk = 35
        versionCode = (System.getenv("GITHUB_RUN_NUMBER") ?: "1").toInt()
        versionName = "0.1." + (System.getenv("GITHUB_RUN_NUMBER") ?: "0")
    }

    // A fixed key so each CI build installs over the last one. The repo is public, so
    // the key is not checked in: CI writes it from the SEATS_KEYSTORE_B64 secret and
    // passes its path and password in SEATS_KEYSTORE and SEATS_KEYSTORE_PASSWORD.
    // Without them (a fork, or a local build) the APK gets the Android debug key.
    val sideloadKeystore = System.getenv("SEATS_KEYSTORE")?.let { file(it) }?.takeIf { it.exists() }
    signingConfigs {
        if (sideloadKeystore != null) {
            create("sideload") {
                storeFile = sideloadKeystore
                storePassword = System.getenv("SEATS_KEYSTORE_PASSWORD")
                keyAlias = "seats"
                keyPassword = System.getenv("SEATS_KEYSTORE_PASSWORD")
            }
        }
    }

    buildTypes {
        val signing = if (sideloadKeystore != null) signingConfigs.getByName("sideload") else signingConfigs.getByName("debug")
        getByName("release") {
            isMinifyEnabled = false
            signingConfig = signing
        }
        getByName("debug") {
            signingConfig = signing
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    testImplementation("junit:junit:4.13.2")
}
