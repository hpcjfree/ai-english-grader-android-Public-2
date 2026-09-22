# AI 英语作文批改 — Android APK 版（基于 V3 DeepSeek 400 修复版）

这是把桌面/局域网版 V3 改造成的 Android 单机应用工程。手机本身运行 Python 后端、SQLite 和本地 Web 服务，WebView 只访问 `127.0.0.1:9449`，不需要电脑服务器，也不包含 Windows 热点逻辑。

## 保留的 V3 功能

- DeepSeek 默认 `https://api.deepseek.com` + `deepseek-flash`
- OpenAI Chat Completions 兼容 Base URL / API Key / 模型名
- DeepSeek 视觉请求、关闭 thinking、JSON Output 回退
- 400/422 时显示模型 API 返回的真实错误正文
- Token 输入 / 输出 / 总量统计
- 新建批改组、题目/范文 OCR、连续作文批改、长按 5 秒结束
- 分数、简短评价、按严重程度排序的语病、可修改内容
- 多套批改微调方案
- 多套教师批改风格学习
- 历史记录及作文图片预览
- SQLite 本地持久化
- V3 Material You 响应式页面

## Android 专用改动

- App 内部启动 `127.0.0.1:9449`，端口不再用于给其他设备访问。
- `<input type=file>` 已接 Android 原生相机 / 相册选择器。
- 相册支持多选；相机可直接拍照。
- Android 原生层先读取 EXIF 方向、最长边缩至 3200px，再统一转 JPEG 92%，之后 Python 层再次规范化，避免 HEIC/PNG/RGBA/方向信息导致视觉 API 400。
- 数据位置为 App 私有目录。卸载 App 会删除数据库和历史图片；升级安装同一 applicationId 的新 APK 则会保留。
- 仅构建 `arm64-v8a`，适合绝大多数现代 Android 手机和平板。
- `minSdk 24`：Android 7.0 及以上。

## 在 Android Studio 构建 APK

1. 安装最新版 Android Studio、Android SDK Platform 36 / Build Tools 36，以及 Python 3.13 x64。
2. 用 Android Studio 打开本目录。
3. 等待 Gradle Sync 完成。第一次会下载 Gradle、Chaquopy、Flask、Requests、Pillow 等依赖。
4. 菜单 `Build > Build APK(s)`。
5. 调试 APK 位于：`app/build/outputs/apk/debug/app-debug.apk`。

Windows 也可以在 Android Studio Terminal 中直接运行：

```bat
BUILD_APK_WINDOWS.bat
```

成功后根目录会出现 `AI英语作文批改-v3-android.apk`。

## GitHub Actions 自动构建

工程已经包含 `.github/workflows/build-apk.yml`。把整个目录提交到 GitHub 后，打开 Actions → `Build Android APK` → Run workflow，即可得到一个名为 `AI-English-Grader-APK` 的可安装 debug APK artifact。

## 第一次使用

安装后打开 App → 设置：

- 提供商：`DeepSeek`
- Base URL：`https://api.deepseek.com`
- 视觉模型：`deepseek-flash`
- API Key：填写自己的 DeepSeek Key
- 网络：默认“直连”

保存后点“测试视觉模型”。成功后即可新建批改组并直接拍照。

## 安全说明

API Key 保存在应用私有 SQLite 数据库内，不会暴露给网页外部；但当前版本未使用 Android Keystore 对 Key 做二次加密。如果手机存在 root/调试取证风险，正式发布版建议再增加 Keystore 加密。
