# Third-party static resource provenance

## Chat Markdown

- `../markdown-it.vendor.js`: the official standalone browser ESM distribution `dist/browser/markdown-it.esm.min.mjs` from markdown-it **15.0.2**, renamed only to reuse project static JS routing; contents are unchanged. Downloaded from the [official npm package](https://registry.npmjs.org/markdown-it/-/markdown-it-15.0.2.tgz), with package SHA-512 `q4IGxMv56jCqT4OCRCADBoDP3LO4MhmTXjFbphHPXs4g3j9Xg5RDnxqN8IF/3vIWEU+VCnUq+7JUg/cfy2E6Qw==`.
- Retain upstream copyright headers without adding project function directories to third-party sources. The MIT license resides in `markdown-it-15.0.2/LICENSE`. The referenced source map is not bundled, affecting only developer-tool source mapping.
- The project adapter is `../assistant-markdown.js`. Load the parser from the same origin without runtime CDN, React, or npm build dependencies. On upgrades, recheck protocol restrictions, image handling, and browser rendering security tests.

## Maps

- `leaflet-1.9.4/leaflet.js`, `leaflet.css`: official Leaflet **1.9.4** distribution files from `https://unpkg.com/leaflet@1.9.4/dist/`. Retain upstream copyright headers without project function directories. The complete BSD-2-Clause license is in adjacent `LICENSE`. The project uses DivIcon rather than default image markers/layer selectors.
- `../world-countries.geojson`: Natural Earth **v5.1.2 / 1:110m admin 0 countries**, from `https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_110m_admin_0_countries.geojson`. Retain only geometry and ADMIN names; remove Antarctica for the news view. Other geographic boundaries are neither hand-drawn nor modified.
- Natural Earth data is public domain: [terms of use](https://www.naturalearthdata.com/about/terms-of-use/). Pages retain attribution. This low-resolution map provides news-location overviews, not navigation or precise boundary decisions.

All runtime resources are served from the application origin, without third-party map-tile requests or API keys. Basemap/component loading failures display explicit errors without switching providers. Leaflet's trailing source-map comment serves developer tools only; source maps and default icon images are not bundled.
