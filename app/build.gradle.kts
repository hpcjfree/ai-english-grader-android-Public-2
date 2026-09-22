plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

android {
    namespace = "com.ai.grader"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.ai.grader"
        minSdk = 24
        targetSdk = 36
        versionCode = 1
        versionName = "1.0.0-v3"

        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

chaquopy {
    defaultConfig {
        version = "3.13"
        pip {
            install("Flask==3.1.2")
            install("requests[socks]==2.32.5")
            install("Pillow==11.3.0")
        }
    }
}

dependencies {
    implementation("androidx.core:core:1.17.0")
}
