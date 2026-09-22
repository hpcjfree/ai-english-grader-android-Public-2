@echo off
setlocal
set APP_HOME=%~dp0
set WRAPPER_JAR=%APP_HOME%gradle\wrapper\gradle-wrapper.jar
set WRAPPER_URL=https://raw.githubusercontent.com/gradle/gradle/v9.4.1/gradle/wrapper/gradle-wrapper.jar
set WRAPPER_SHA=55243ef57851f12b070ad14f7f5bb8302daceeebc5bce5ece5fa6edb23e1145c
if not exist "%WRAPPER_JAR%" (
  echo Downloading official Gradle 9.4.1 wrapper...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -UseBasicParsing '%WRAPPER_URL%' -OutFile '%WRAPPER_JAR%'"
  if errorlevel 1 exit /b 1
)
for /f "tokens=*" %%H in ('powershell -NoProfile -Command "(Get-FileHash '%WRAPPER_JAR%' -Algorithm SHA256).Hash.ToLower()"') do set ACTUAL_SHA=%%H
if /I not "%ACTUAL_SHA%"=="%WRAPPER_SHA%" (
  echo Gradle wrapper checksum mismatch.
  exit /b 1
)
java %JAVA_OPTS% %GRADLE_OPTS% -Dorg.gradle.appname=gradlew -classpath "%WRAPPER_JAR%" org.gradle.wrapper.GradleWrapperMain %*
endlocal
