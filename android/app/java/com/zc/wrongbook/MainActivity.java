package com.zc.wrongbook;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.DownloadListener;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

/**
 * 408 错题本安卓端：一个 WebView 壳，套的是 web/ 那份 H5。
 *
 * 相比"再写一套原生界面"，这样最省事也最不容易走样：
 * <ul>
 *   <li>服务器地址可在 App 内配置（存 SharedPreferences，不用重新打包）；</li>
 *   <li>导出 Word 走系统 DownloadManager，存到「下载」目录并在通知栏可打开；</li>
 *   <li>管理后台也能在这个 WebView 里用（支持 &lt;input type=file&gt; 选 PDF）；</li>
 *   <li>不依赖 androidx / Gradle，只用系统 API，便于手工打包成 APK。</li>
 * </ul>
 */
public class MainActivity extends Activity {

    private static final String PREFS = "zc_wrongbook";
    private static final String PREF_URL = "server_url";
    /** 只是输入框里的示例默认值，用户可以改成自己电脑的局域网 IP。 */
    private static final String FALLBACK_URL = "http://192.168.1.100:18100";
    private static final int REQ_FILE = 1001;
    private static final int REQ_STORAGE = 1002;

    private SharedPreferences prefs;
    private WebView web;
    private ProgressBar bar;
    private LinearLayout banner;
    private TextView bannerText;

    private ValueCallback<Uri[]> fileCallback;
    private String queuedUrl;
    private String queuedName;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        setContentView(buildUi());
        configureWebView();

        String url = prefs.getString(PREF_URL, "");
        if (url.isEmpty()) {
            showServerDialog(true);   // 第一次启动先让用户填服务器地址
        } else {
            load(url);
        }
    }

    // ------------------------------------------------------------ 界面（纯代码搭，省掉 layout xml）
    private View buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.WHITE);

        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.HORIZONTAL);
        top.setGravity(Gravity.CENTER_VERTICAL);
        top.setBackgroundColor(Color.parseColor("#2F6FED"));
        top.setPadding(dp(12), dp(6), dp(6), dp(6));

        TextView title = new TextView(this);
        title.setText(R.string.app_name);
        title.setTextColor(Color.WHITE);
        title.setTextSize(17);
        title.setSingleLine(true);
        title.setLayoutParams(new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        Button serverBtn = new Button(this);
        serverBtn.setText("服务器");
        serverBtn.setAllCaps(false);
        serverBtn.setOnClickListener(v -> showServerDialog(false));

        Button refreshBtn = new Button(this);
        refreshBtn.setText("刷新");
        refreshBtn.setAllCaps(false);
        refreshBtn.setOnClickListener(v -> {
            hideBanner();
            web.reload();
        });

        top.addView(title);
        top.addView(serverBtn);
        top.addView(refreshBtn);

        bar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        bar.setMax(100);
        bar.setVisibility(View.GONE);

        web = new WebView(this);

        banner = new LinearLayout(this);
        banner.setOrientation(LinearLayout.HORIZONTAL);
        banner.setGravity(Gravity.CENTER_VERTICAL);
        banner.setBackgroundColor(Color.parseColor("#FDECEC"));
        banner.setPadding(dp(12), dp(8), dp(6), dp(8));
        banner.setVisibility(View.GONE);
        bannerText = new TextView(this);
        bannerText.setTextColor(Color.parseColor("#C0392B"));
        bannerText.setTextSize(13);
        bannerText.setLayoutParams(new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        Button fixBtn = new Button(this);
        fixBtn.setText("改地址");
        fixBtn.setAllCaps(false);
        fixBtn.setOnClickListener(v -> showServerDialog(false));
        banner.addView(bannerText);
        banner.addView(fixBtn);

        root.addView(top);
        root.addView(bar);
        root.addView(web, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        root.addView(banner);
        return root;
    }

    private void configureWebView() {
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);   // H5 用 localStorage 记勾选状态
        settings.setDatabaseEnabled(true);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setSupportZoom(false);
        settings.setBuiltInZoomControls(false);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        settings.setUserAgentString(settings.getUserAgentString() + " ZCWrongBook/1.0");
        WebView.setWebContentsDebuggingEnabled(true);

        // 登录态存在 Cookie 里（图片和 Word 下载链接只能靠它带），确认 WebView 接受并持久化
        CookieManager cookies = CookieManager.getInstance();
        cookies.setAcceptCookie(true);
        cookies.setAcceptThirdPartyCookies(web, true);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return handleUrl(request.getUrl().toString());
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                bar.setVisibility(View.GONE);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    bar.setVisibility(View.GONE);
                    showBanner("连不上服务器（" + error.getDescription() + "），请检查地址和是否同一局域网");
                }
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int progress) {
                bar.setVisibility(progress >= 100 ? View.GONE : View.VISIBLE);
                bar.setProgress(progress);
            }

            /** 管理后台上传 PDF 要用到 <input type="file"> */
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                if (fileCallback != null) {
                    fileCallback.onReceiveValue(null);
                }
                fileCallback = callback;
                Intent intent = new Intent(Intent.ACTION_GET_CONTENT);
                intent.addCategory(Intent.CATEGORY_OPENABLE);
                intent.setType(pickMime(params.getAcceptTypes()));
                try {
                    startActivityForResult(Intent.createChooser(intent, "选择文件"), REQ_FILE);
                } catch (Exception e) {
                    fileCallback = null;
                    return false;
                }
                return true;
            }
        });

        web.setDownloadListener(new DownloadListener() {
            @Override
            public void onDownloadStart(String url, String userAgent, String contentDisposition,
                                        String mimeType, long contentLength) {
                download(url, URLUtil.guessFileName(url, contentDisposition, mimeType));
            }
        });
    }

    // ------------------------------------------------------------ 导航 / 下载
    private boolean handleUrl(String url) {
        if (url == null) {
            return false;
        }
        // 错题本下载链接交给系统下载器（WebView 里点 <a download> 不一定触发）
        if (url.contains("/api/exports/") && url.endsWith("/download")) {
            download(url, "");
            return true;
        }
        if (url.startsWith("http://") || url.startsWith("https://")) {
            return false;   // 站内页面留在 WebView
        }
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
        } catch (Exception ignored) {
            // 没有对应 App，忽略
        }
        return true;
    }

    private void download(String url, String fileName) {
        if (fileName == null || fileName.isEmpty()) {
            fileName = "408错题本.docx";
        }
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q
                && checkSelfPermission(android.Manifest.permission.WRITE_EXTERNAL_STORAGE)
                != PackageManager.PERMISSION_GRANTED) {
            queuedUrl = url;
            queuedName = fileName;
            requestPermissions(new String[]{android.Manifest.permission.WRITE_EXTERNAL_STORAGE}, REQ_STORAGE);
            return;
        }
        enqueue(url, fileName);
    }

    private void enqueue(String url, String fileName) {
        try {
            DownloadManager.Request request = new DownloadManager.Request(Uri.parse(url));
            request.setTitle(fileName);
            request.setDescription("408 错题本");
            request.setMimeType("application/vnd.openxmlformats-officedocument.wordprocessingml.document");
            request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, fileName);
            DownloadManager manager = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
            manager.enqueue(request);
            Toast.makeText(this, "正在下载：" + fileName + "\n完成后可在通知栏打开", Toast.LENGTH_LONG).show();
        } catch (Exception e) {
            Toast.makeText(this, "下载失败：" + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQ_STORAGE) {
            if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED
                    && queuedUrl != null) {
                enqueue(queuedUrl, queuedName);
            } else {
                Toast.makeText(this, "没有存储权限，无法保存文件", Toast.LENGTH_LONG).show();
            }
            queuedUrl = null;
            queuedName = null;
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == REQ_FILE) {
            Uri[] result = null;
            if (resultCode == RESULT_OK && data != null && data.getData() != null) {
                result = new Uri[]{data.getData()};
            }
            if (fileCallback != null) {
                fileCallback.onReceiveValue(result);
                fileCallback = null;
            }
            return;
        }
        super.onActivityResult(requestCode, resultCode, data);
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        // 把内存里的 Cookie 落盘，下次打开还是登录态
        CookieManager.getInstance().flush();
    }

    // ------------------------------------------------------------ 服务器地址
    private void showServerDialog(final boolean firstRun) {
        final EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        input.setSingleLine(true);
        input.setText(prefs.getString(PREF_URL, FALLBACK_URL));
        input.setSelection(input.getText().length());

        FrameLayout holder = new FrameLayout(this);
        FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.leftMargin = dp(20);
        lp.rightMargin = dp(20);
        holder.addView(input, lp);

        AlertDialog.Builder builder = new AlertDialog.Builder(this)
                .setTitle("服务器地址")
                .setMessage("填运行后端那台电脑的地址，手机要和电脑在同一局域网。\n例如 http://192.168.1.5:18100")
                .setView(holder)
                .setPositiveButton("保存并连接", (dialog, which) -> {
                    String url = normalize(input.getText().toString());
                    prefs.edit().putString(PREF_URL, url).apply();
                    hideBanner();
                    load(url);
                });
        if (firstRun) {
            builder.setNegativeButton("退出", (dialog, which) -> finish());
            builder.setCancelable(false);
        } else {
            builder.setNegativeButton("取消", null);
        }
        builder.show();
    }

    private String normalize(String raw) {
        String url = raw == null ? "" : raw.trim();
        if (url.isEmpty()) {
            return prefs.getString(PREF_URL, FALLBACK_URL);
        }
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            url = "http://" + url;
        }
        while (url.endsWith("/")) {
            url = url.substring(0, url.length() - 1);
        }
        return url;
    }

    private void load(String url) {
        bar.setVisibility(View.VISIBLE);
        bar.setProgress(0);
        web.loadUrl(url);
    }

    private void showBanner(String text) {
        bannerText.setText(text);
        banner.setVisibility(View.VISIBLE);
    }

    private void hideBanner() {
        banner.setVisibility(View.GONE);
    }

    private String pickMime(String[] acceptTypes) {
        if (acceptTypes != null) {
            for (String type : acceptTypes) {
                if (type == null) {
                    continue;
                }
                if (type.contains("pdf")) {
                    return "application/pdf";
                }
                if (type.contains("image")) {
                    return "image/*";
                }
            }
        }
        return "*/*";
    }

    private int dp(int value) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, value,
                getResources().getDisplayMetrics());
    }
}
