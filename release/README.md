# 热榜深潜简报 · 公网发布包

打包时间：2026-11-21 深夜轮。生成脚本见补丁目录 `patch_release/build.py`（可重复执行）。

## 目录

```
release/
  public/        网站本体，扔到任意静态托管即可（GitHub Pages / Vercel / 服务器 nginx）
    index.html
    data.js      已标记为 source:"demo"，页面徽章显示「示例数据」
    assets/      吉祥物为动画 WebP（GIF 已弃用）：总计约 1MB，带透明通道
  extension/     Chrome 插件（加载已解压扩展即可用）
```

## 上线前必须拍板的两件事

1. **数据来源（最重要）**：`public/data.js` 是手策展的演示快照。
   公网放它必须保持 `source: "demo"`（徽章会显示「示例数据」，notice 已写明）。
   如果要放真实数据，替换 data.js 并相应改 source/cache 标记；
   旧版抓取规则备份在 `demo/data.js.demo-bak`（需先评估是否恢复）。

2. **插件跳转地址**：`extension/data.js` 里的 `site_url` 现在是
   `http://localhost:7100/`，上线后改成正式域名，再运行
   `node extension/gen_extension_data.js` 重新生成。

## 部署

- `public/` 是纯静态站点，无构建步骤、无后端依赖（PERSONA_ENDPOINT
  未配置时身份视角功能自动走内置兜底文案）。
- 唯一外部依赖是知乎吉祥物刘看山的 GIF/PNG 素材，已随包携带。
