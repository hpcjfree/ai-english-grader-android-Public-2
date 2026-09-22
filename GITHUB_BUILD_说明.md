# GitHub Actions 云端编译 APK

这个版本已经为 GitHub Actions 云端编译做了专门处理，不要求你的电脑安装 Android Studio、Android SDK 或 Gradle。

## 最简单的使用方式

1. 在 GitHub 新建一个空仓库，例如 `ai-english-grader-android`。
2. 将本目录中的**全部文件和文件夹**上传到仓库根目录。
   - `.github` 目录必须一起上传。
   - `app`、`build.gradle.kts`、`settings.gradle.kts` 等必须直接位于仓库根目录。
3. 将默认分支设为 `main`。
4. 打开仓库顶部的 **Actions**。
5. 左侧选择 **Build Android APK**。
6. 点击 **Run workflow** → **Run workflow**。
7. 构建完成后进入该次运行记录，在页面底部 **Artifacts** 下载：
   - `AI-English-Grader-v3-APK`
8. 解压 Artifact，得到：
   - `AI-English-Grader-v3.apk`
   - `AI-English-Grader-v3.apk.sha256`
9. 将 APK 发送到安卓手机安装即可。

## 自动构建

除了手动 `Run workflow`，每次向 `main` 分支 push 代码也会自动重新编译 APK。

## GitHub 云端会自动安装

- Ubuntu runner
- JDK 17
- Python 3.13
- Android SDK Platform 36
- Android Build Tools 36.0.0
- Gradle 9.4.1

然后执行：

```text
gradle :app:assembleDebug --stacktrace --no-daemon
```

最终 APK 是 Android Debug 签名的可安装包，无需你配置签名证书。

## 注意

DeepSeek API Key **不会写进 GitHub 仓库，也不需要放 GitHub Secrets**。安装 APK 后，在 App 的“设置”页面里填写 API Key 即可。

如果 Actions 页面没有显示工作流，请确认 `.github/workflows/build-apk.yml` 已经上传到仓库，并且 GitHub Actions 没有在仓库设置中被禁用。
