# web/ 静态资源

- `nocturne.css` — 设计系统本体，上游 claude.ai/design 项目 `02140514-9ffd-4a59-adcb-2abd779c42ee`（Nocturne）。重新同步用 `DesignSync`（`get_file`，`path=styles.css`）拉取后原样覆盖，但保留文件头注释里说明的那处删除（Google Fonts `@import`，离线自包含要求）。
- `app.css` — 本工具的页面层样式（侧边栏/两栏工作区/卡片网格），改这个，不要改 `nocturne.css`。
- `icons.svg` — Phosphor 图标 sprite，用到的图标名见 `serve_audio.py` 里引用 `#ph-*` 的地方；补图标就去 `cdn.jsdelivr.net/npm/@phosphor-icons/core@2/assets/regular/<name>.svg` 抓，拼进 `<symbol id="ph-<name>">`。
