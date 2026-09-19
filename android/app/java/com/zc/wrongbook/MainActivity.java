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
 *   <li>导出 Word：页面会用登录态换一条**15 分钟有效的临时下载链接**并弹面板，可「复制链接」或
 *       「用浏览器打开」；壳拦到下载 URL 就用系统浏览器打开。复制走原生剪贴板（window.ZCAndroid.copy），
 *       因为 http 下浏览器剪贴板 API 不可用；没装浏览器时才退回系统 DownloadManager（带上 Cookie 头）；</li>
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
            reloadFresh();
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

    /**
     * 顶部「刷新」：先清掉 WebView 的 HTTP 缓存再 reload。
     *
     * 直接 reload() 时 WebView 可能仍拿缓存里的旧脚本，用户会遇到"服务端改了、App 里没变"
     * （后端已经给静态资源加 no-cache + 版本戳，这里再兜一层，免得手机上只能靠清应用数据）。
     */
    private void reloadFresh() {
        try {
            web.clearCache(true);
        } catch (Exception ignored) {
            // 清缓存失败不该挡住刷新
        }
        web.reload();
    }

    /** 给网页用的剪贴板桥（window.ZCAndroid.copy）。必须跑在主线程上弹 Toast。 */
    private class ClipboardBridge {
        @android.webkit.JavascriptInterface
        public void copy(final String text) {
            if (text == null || text.isEmpty()) {
                return;
            }
            runOnUiThread(() -> {
                try {
                    android.content.ClipboardManager manager =
                            (android.content.ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
                    manager.setPrimaryClip(android.content.ClipData.newPlainText("408错题本下载链接", text));
                    Toast.makeText(MainActivity.this, "下载链接已复制，粘到浏览器就能下载", Toast.LENGTH_LONG).show();
                } catch (Exception e) {
                    Toast.makeText(MainActivity.this, "复制失败：" + e.getMessage(), Toast.LENGTH_LONG).show();
                }
            });
        }
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
        // 前端是无构建的（改 web/*.js 就生效），别让 WebView 自作主张用旧缓存
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        settings.setUserAgentString(settings.getUserAgentString() + " ZCWrongBook/1.0");
        WebView.setWebContentsDebuggingEnabled(true);

        // 登录态存在 Cookie 里（图片和 Word 下载链接只能靠它带），确认 WebView 接受并持久化
        CookieManager cookies = CookieManager.getInstance();
        cookies.setAcceptCookie(true);
        cookies.setAcceptThirdPartyCookies(web, true);

        // 页面用它把「临时下载链接」写进系统剪贴板：
        // http 下 navigator.clipboard 用不了（不是安全上下文），execCommand 在部分 WebView 也不灵，
        // 原生 ClipboardManager 最稳。JS 里叫 window.ZCAndroid.copy(text)。
        web.addJavascriptInterface(new ClipboardBridge(), "ZCAndroid");

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
                // 一律交给系统浏览器下载：WebView 里下到的东西在手机上不好找、也打不开
                openInBrowser(url);
            }
        });
    }

    // ------------------------------------------------------------ 导航 / 下载
    private boolean handleUrl(String url) {
        if (url == null) {
            return false;
        }
        // 错题本下载链接（页面会带上一次性票）交给系统浏览器：浏览器会存到「下载」目录，
        // 下完直接能选 WPS/Word 打开；不再用 DownloadManager（它拿不到登录 Cookie，会下到 401 的 JSON）。
        if (url.contains("/api/exports/") && url.contains("/download")) {
            openInBrowser(url);
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

    /** 用系统浏览器打开（下载 Word 用）。没装浏览器时退回系统下载器（会带上登录 Cookie）。 */
    private void openInBrowser(String url) {
        try {
            Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(intent);
            Toast.makeText(this, "已交给浏览器下载（完成后在「下载」里打开）", Toast.LENGTH_LONG).show();
        } catch (Exception e) {
            Toast.makeText(this, "没找到浏览器，改用系统下载器", Toast.LENGTH_SHORT).show();
            download(url, "");
        }
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
            // 下载器是系统进程，不会自动带 WebView 的 Cookie —— 不带就是下到一份 401 的 JSON
            String cookie = CookieManager.getInstance().getCookie(url);
            if (cookie != null && !cookie.isEmpty()) {
                request.addRequestHeader("Cookie", cookie);
            }
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
