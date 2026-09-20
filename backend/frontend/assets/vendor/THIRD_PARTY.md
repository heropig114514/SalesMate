# 第三方静态资源来源

## 聊天 Markdown

- `../markdown-it.vendor.js`：markdown-it **15.0.2** 官方独立浏览器 ESM 发行文件 `dist/browser/markdown-it.esm.min.mjs`，仅重命名以复用项目静态 JS 路由，内容未修改。下载自 [npm 官方包](https://registry.npmjs.org/markdown-it/-/markdown-it-15.0.2.tgz)，包 SHA-512 校验值为 `q4IGxMv56jCqT4OCRCADBoDP3LO4MhmTXjFbphHPXs4g3j9Xg5RDnxqN8IF/3vIWEU+VCnUq+7JUg/cfy2E6Qw==`。
- 上游版权头保留，不对第三方源码添加项目函数目录；MIT 许可证保存在 `markdown-it-15.0.2/LICENSE`。发行文件引用的 source map 未捆绑，仅影响开发工具源码映射。
- 项目适配层为 `../assistant-markdown.js`。解析器同源加载，不依赖运行时 CDN、React 或 npm 构建。升级时重新核对协议限制、图片处理与浏览器渲染安全测试。

## 地图

- `leaflet-1.9.4/leaflet.js`、`leaflet.css`：Leaflet **1.9.4** 官方发行文件，下载自 `https://unpkg.com/leaflet@1.9.4/dist/`。上游版权头保留，不对第三方源码添加项目函数目录。许可证为 BSD-2-Clause，完整文本保存在同目录 `LICENSE`。项目使用 DivIcon，不使用默认图片标记或图层选择器。
- `../world-countries.geojson`：Natural Earth **v5.1.2 / 1:110m admin 0 countries**，来源 `https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_110m_admin_0_countries.geojson`。只保留几何和 ADMIN 名称，移除南极以适配消息视图；未手绘或修改其余地理边界。
- Natural Earth 数据为公共领域：[使用条款](https://www.naturalearthdata.com/about/terms-of-use/)。页面保留数据来源署名。该低分辨率地图仅用于消息位置概览，不用于导航或精确边界判断。

全部运行资源由应用同源提供，不向第三方请求地图瓦片，也不需要 API Key。底图与组件加载失败时明确显示错误，不切换第三方资源。Leaflet 发行文件末尾的 source map 注释仅用于开发工具；项目未捆绑源码映射或默认图标图片。
