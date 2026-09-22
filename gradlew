#!/bin/sh
set -eu
APP_HOME=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
WRAPPER_JAR="$APP_HOME/gradle/wrapper/gradle-wrapper.jar"
WRAPPER_URL="https://raw.githubusercontent.com/gradle/gradle/v9.4.1/gradle/wrapper/gradle-wrapper.jar"
WRAPPER_SHA="55243ef57851f12b070ad14f7f5bb8302daceeebc5bce5ece5fa6edb23e1145c"
if [ ! -f "$WRAPPER_JAR" ]; then
  echo "Downloading official Gradle 9.4.1 wrapper..."
  mkdir -p "$(dirname "$WRAPPER_JAR")"
  if command -v curl >/dev/null 2>&1; then curl -fL "$WRAPPER_URL" -o "$WRAPPER_JAR";
  elif command -v wget >/dev/null 2>&1; then wget -O "$WRAPPER_JAR" "$WRAPPER_URL";
  else echo "curl or wget is required" >&2; exit 1; fi
fi
if command -v sha256sum >/dev/null 2>&1; then
  echo "$WRAPPER_SHA  $WRAPPER_JAR" | sha256sum -c - >/dev/null || { echo "Gradle wrapper checksum mismatch" >&2; exit 1; }
fi
exec java ${JAVA_OPTS:-} ${GRADLE_OPTS:-} -Dorg.gradle.appname=gradlew -classpath "$WRAPPER_JAR" org.gradle.wrapper.GradleWrapperMain "$@"
