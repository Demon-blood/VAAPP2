$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$App = Join-Path $Root 'android'

if (-not (Get-Command flutter -ErrorAction SilentlyContinue)) {
    throw 'Flutter is not installed or is not available in PATH.'
}

$required = @('ANDROID_KEYSTORE_PATH','ANDROID_KEYSTORE_PASSWORD','ANDROID_KEY_ALIAS','ANDROID_KEY_PASSWORD')
foreach ($name in $required) {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) {
        throw "$name is required. Release builds must use the persistent signing key; debug signing is intentionally disabled."
    }
}

Push-Location $App
try {
    $Manifest = Join-Path $App 'android\app\src\main\AndroidManifest.xml'
    $ManifestBackup = Join-Path $env:TEMP 'full_time_va_AndroidManifest.xml'
    if (Test-Path $Manifest) { Copy-Item $Manifest $ManifestBackup -Force }

    flutter create --platforms=android --org com.fulltimeva --project-name full_time_va .

    if (Test-Path $ManifestBackup) {
        New-Item -ItemType Directory -Path (Split-Path $Manifest -Parent) -Force | Out-Null
        Copy-Item $ManifestBackup $Manifest -Force
    }

    $GradleTemplate = Join-Path $App 'tooling\app-build.gradle.kts'
    $GradleTarget = Join-Path $App 'android\app\build.gradle.kts'
    if (-not (Test-Path $GradleTemplate)) { throw 'Missing Android Gradle template.' }
    Copy-Item $GradleTemplate $GradleTarget -Force

    flutter pub get
    flutter analyze
    flutter test
    flutter build apk --release

    $Apk = Join-Path $App 'build\app\outputs\flutter-apk\app-release.apk'
    Write-Host "APK created: $Apk" -ForegroundColor Green
}
finally {
    Pop-Location
}
