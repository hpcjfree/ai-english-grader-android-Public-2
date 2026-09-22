package com.ai.grader;

import android.app.Activity;
import android.content.ClipData;
import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Matrix;
import android.media.ExifInterface;
import android.net.Uri;
import android.os.Bundle;
import android.provider.MediaStore;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import androidx.core.content.FileProvider;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    private static final int FILE_CHOOSER_REQUEST = 9449;
    private static final String LOCAL_URL = "http://127.0.0.1:9449/";

    private WebView webView;
    private ProgressBar progress;
    private TextView loadingText;
    private ValueCallback<Uri[]> filePathCallback;
    private Uri cameraOutputUri;
    private final ExecutorService executor = Executors.newCachedThreadPool();

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        buildLoadingUi();

        executor.execute(() -> {
            try {
                copyWebAssets();
                startPythonServer();
                if (!waitForServer(30000)) {
                    throw new RuntimeException("本地批改服务启动超时");
                }
                runOnUiThread(this::showWebView);
            } catch (Exception e) {
                runOnUiThread(() -> showFatalError(e));
            }
        });
    }

    private void buildLoadingUi() {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(0xFFFDF8FF);

        progress = new ProgressBar(this);
        FrameLayout.LayoutParams pp = new FrameLayout.LayoutParams(96, 96);
        pp.gravity = android.view.Gravity.CENTER;
        pp.topMargin = -90;
        root.addView(progress, pp);

        loadingText = new TextView(this);
        loadingText.setText("正在启动 AI 英语作文批改…");
        loadingText.setTextSize(16f);
        loadingText.setTextColor(0xFF49454F);
        loadingText.setGravity(android.view.Gravity.CENTER);
        FrameLayout.LayoutParams tp = new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.WRAP_CONTENT);
        tp.gravity = android.view.Gravity.CENTER;
        tp.topMargin = 80;
        tp.leftMargin = 32;
        tp.rightMargin = 32;
        root.addView(loadingText, tp);
        setContentView(root);
    }

    private void showFatalError(Exception e) {
        progress.setVisibility(View.GONE);
        loadingText.setText("启动失败\n\n" + e.getClass().getSimpleName() + ": " + e.getMessage() +
                "\n\n请完全退出应用后重新打开。若仍失败，请把这段文字发给开发者。");
    }

    private void showWebView() {
        webView = new WebView(this);
        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setAllowFileAccess(true);
        s.setAllowContentAccess(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        s.setUserAgentString(s.getUserAgentString() + " AIEnglishGraderAndroid/1.0");

        webView.setWebViewClient(new WebViewClient());
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView webView,
                                             ValueCallback<Uri[]> filePathCallbackNew,
                                             FileChooserParams fileChooserParams) {
                if (filePathCallback != null) filePathCallback.onReceiveValue(null);
                filePathCallback = filePathCallbackNew;
                launchImageChooser(fileChooserParams);
                return true;
            }
        });
        setContentView(webView);
        webView.loadUrl(LOCAL_URL);
    }

    private void launchImageChooser(WebChromeClient.FileChooserParams params) {
        try {
            boolean allowMultiple = params != null && params.getMode() == WebChromeClient.FileChooserParams.MODE_OPEN_MULTIPLE;
            Intent gallery = new Intent(Intent.ACTION_GET_CONTENT);
            gallery.addCategory(Intent.CATEGORY_OPENABLE);
            gallery.setType("image/*");
            gallery.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, allowMultiple);

            File cameraDir = new File(getCacheDir(), "camera");
            if (!cameraDir.exists() && !cameraDir.mkdirs()) throw new IOException("无法创建相机缓存目录");
            File cameraFile = new File(cameraDir, "capture_" + System.currentTimeMillis() + ".jpg");
            cameraOutputUri = FileProvider.getUriForFile(this, getPackageName() + ".fileprovider", cameraFile);

            Intent camera = new Intent(MediaStore.ACTION_IMAGE_CAPTURE);
            camera.putExtra(MediaStore.EXTRA_OUTPUT, cameraOutputUri);
            camera.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION | Intent.FLAG_GRANT_READ_URI_PERMISSION);

            Intent chooser = Intent.createChooser(gallery, "拍照或选择图片");
            if (camera.resolveActivity(getPackageManager()) != null) {
                chooser.putExtra(Intent.EXTRA_INITIAL_INTENTS, new Intent[]{camera});
            }
            startActivityForResult(chooser, FILE_CHOOSER_REQUEST);
        } catch (Exception e) {
            if (filePathCallback != null) filePathCallback.onReceiveValue(null);
            filePathCallback = null;
            cameraOutputUri = null;
            showFatalError(new RuntimeException("无法打开相机/相册：" + e.getMessage(), e));
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != FILE_CHOOSER_REQUEST || filePathCallback == null) return;
        if (resultCode != RESULT_OK) {
            filePathCallback.onReceiveValue(null);
            filePathCallback = null;
            cameraOutputUri = null;
            return;
        }

        final List<Uri> selected = new ArrayList<>();
        if (data != null) {
            ClipData clip = data.getClipData();
            if (clip != null) {
                for (int i = 0; i < clip.getItemCount(); i++) selected.add(clip.getItemAt(i).getUri());
            } else if (data.getData() != null) {
                selected.add(data.getData());
            }
        }
        if (selected.isEmpty() && cameraOutputUri != null) selected.add(cameraOutputUri);

        executor.execute(() -> {
            List<Uri> normalized = new ArrayList<>();
            for (Uri uri : selected) {
                try {
                    normalized.add(normalizeToJpeg(uri));
                } catch (Exception ignored) {
                    // Keep the original URI as a fallback. The Python layer will show a clear format error if needed.
                    normalized.add(uri);
                }
            }
            runOnUiThread(() -> {
                if (filePathCallback != null) {
                    filePathCallback.onReceiveValue(normalized.toArray(new Uri[0]));
                    filePathCallback = null;
                }
                cameraOutputUri = null;
            });
        });
    }

    private Uri normalizeToJpeg(Uri source) throws Exception {
        Bitmap bitmap;
        try (InputStream in = getContentResolver().openInputStream(source)) {
            if (in == null) throw new IOException("无法读取图片");
            bitmap = BitmapFactory.decodeStream(in);
        }
        if (bitmap == null) throw new IOException("Android 无法解码该图片格式");

        int orientation = ExifInterface.ORIENTATION_NORMAL;
        try (InputStream exifIn = getContentResolver().openInputStream(source)) {
            if (exifIn != null) {
                ExifInterface exif = new ExifInterface(exifIn);
                orientation = exif.getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL);
            }
        } catch (Exception ignored) {}

        Matrix matrix = new Matrix();
        if (orientation == ExifInterface.ORIENTATION_ROTATE_90) matrix.postRotate(90);
        else if (orientation == ExifInterface.ORIENTATION_ROTATE_180) matrix.postRotate(180);
        else if (orientation == ExifInterface.ORIENTATION_ROTATE_270) matrix.postRotate(270);
        else if (orientation == ExifInterface.ORIENTATION_FLIP_HORIZONTAL) matrix.preScale(-1, 1);
        else if (orientation == ExifInterface.ORIENTATION_FLIP_VERTICAL) matrix.preScale(1, -1);

        if (!matrix.isIdentity()) {
            Bitmap rotated = Bitmap.createBitmap(bitmap, 0, 0, bitmap.getWidth(), bitmap.getHeight(), matrix, true);
            if (rotated != bitmap) bitmap.recycle();
            bitmap = rotated;
        }

        int maxSide = Math.max(bitmap.getWidth(), bitmap.getHeight());
        if (maxSide > 3200) {
            float scale = 3200f / maxSide;
            int nw = Math.max(1, Math.round(bitmap.getWidth() * scale));
            int nh = Math.max(1, Math.round(bitmap.getHeight() * scale));
            Bitmap scaled = Bitmap.createScaledBitmap(bitmap, nw, nh, true);
            if (scaled != bitmap) bitmap.recycle();
            bitmap = scaled;
        }

        File outDir = new File(getCacheDir(), "normalized");
        if (!outDir.exists() && !outDir.mkdirs()) throw new IOException("无法创建图片缓存目录");
        File out = new File(outDir, "img_" + System.nanoTime() + ".jpg");
        try (FileOutputStream fos = new FileOutputStream(out)) {
            if (!bitmap.compress(Bitmap.CompressFormat.JPEG, 92, fos)) throw new IOException("JPEG 转换失败");
        } finally {
            bitmap.recycle();
        }
        return FileProvider.getUriForFile(this, getPackageName() + ".fileprovider", out);
    }

    private void startPythonServer() {
        if (!Python.isStarted()) Python.start(new AndroidPlatform(this));
        Python py = Python.getInstance();
        PyObject server = py.getModule("server");
        server.callAttr("start_server", getFilesDir().getAbsolutePath());
    }

    private boolean waitForServer(long timeoutMs) {
        long end = System.currentTimeMillis() + timeoutMs;
        while (System.currentTimeMillis() < end) {
            HttpURLConnection c = null;
            try {
                c = (HttpURLConnection) new URL(LOCAL_URL + "api/device").openConnection();
                c.setConnectTimeout(600);
                c.setReadTimeout(600);
                if (c.getResponseCode() == 200) return true;
            } catch (Exception ignored) {
            } finally {
                if (c != null) c.disconnect();
            }
            try { Thread.sleep(200); } catch (InterruptedException ignored) {}
        }
        return false;
    }

    private void copyWebAssets() throws IOException {
        File dest = new File(getFilesDir(), "web");
        copyAssetTree("web", dest);
    }

    private void copyAssetTree(String assetPath, File dest) throws IOException {
        String[] children = getAssets().list(assetPath);
        if (children == null || children.length == 0) {
            File parent = dest.getParentFile();
            if (parent != null && !parent.exists()) parent.mkdirs();
            try (InputStream in = getAssets().open(assetPath); FileOutputStream out = new FileOutputStream(dest, false)) {
                byte[] buf = new byte[32768];
                int n;
                while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            }
            return;
        }
        if (!dest.exists() && !dest.mkdirs()) throw new IOException("无法创建目录：" + dest);
        for (String child : children) copyAssetTree(assetPath + "/" + child, new File(dest, child));
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            webView.stopLoading();
            webView.destroy();
        }
        executor.shutdownNow();
        super.onDestroy();
    }
}
