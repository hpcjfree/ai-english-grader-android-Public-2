@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo ======================================
echo AI英语作文批改 Android APK 构建
echo ======================================
where java >nul 2>nul || (
  echo [错误] 未找到 Java。建议安装 Android Studio，并从 Android Studio Terminal 运行此脚本。
  pause
  exit /b 1
)
where python3.13 >nul 2>nul
if errorlevel 1 where python >nul 2>nul || (
  echo [错误] 未找到 Python 3.13。请安装 Python 3.13 x64。
  pause
  exit /b 1
)
call gradlew.bat :app:assembleDebug
if errorlevel 1 (
  echo [错误] APK 构建失败，请查看上面的错误信息。
  pause
  exit /b 1
)
copy /Y "app\build\outputs\apk\debug\app-debug.apk" "AI英语作文批改-v3-android.apk" >nul
echo.
echo [完成] APK：%CD%\AI英语作文批改-v3-android.apk
echo 可直接发送到 Android 手机安装。
pause
