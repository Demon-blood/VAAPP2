plugins {
    id("com.android.application")
    id("kotlin-android")
    id("dev.flutter.flutter-gradle-plugin")
}

val releaseKeystorePath = System.getenv("ANDROID_KEYSTORE_PATH") ?: ""
val releaseStorePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD") ?: ""
val releaseKeyAlias = System.getenv("ANDROID_KEY_ALIAS") ?: ""
val releaseKeyPassword = System.getenv("ANDROID_KEY_PASSWORD") ?: ""

android {
    namespace = "com.fulltimeva.full_time_va"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        isCoreLibraryDesugaringEnabled = true
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = JavaVersion.VERSION_17.toString()
    }

    defaultConfig {
        applicationId = "com.fulltimeva.full_time_va"
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
        multiDexEnabled = true
    }

    signingConfigs {
        create("release") {
            require(releaseKeystorePath.isNotBlank()) { "ANDROID_KEYSTORE_PATH is required for release builds" }
            require(releaseStorePassword.isNotBlank()) { "ANDROID_KEYSTORE_PASSWORD is required for release builds" }
            require(releaseKeyAlias.isNotBlank()) { "ANDROID_KEY_ALIAS is required for release builds" }
            require(releaseKeyPassword.isNotBlank()) { "ANDROID_KEY_PASSWORD is required for release builds" }
            storeFile = file(releaseKeystorePath)
            storePassword = releaseStorePassword
            keyAlias = releaseKeyAlias
            keyPassword = releaseKeyPassword
            storeType = "PKCS12"
        }
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.getByName("release")
        }
    }
}

flutter {
    source = "../.."
}

dependencies {
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.4")
}
