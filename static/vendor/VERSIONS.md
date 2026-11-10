# Vendored front-end libraries

All files are committed so the app works offline with no Node/npm. To update a library,
download the new tarball from the npm registry (no npm needed), copy the single file below,
update this table and bump `PWA_VERSION` so installed apps pick up the change.

| Library | Version | File(s) | Source |
|---|---|---|---|
| htmx | 2.0.4 | `htmx/htmx.min.js` | https://registry.npmjs.org/htmx.org/-/htmx.org-2.0.4.tgz → `dist/htmx.min.js` |
| Alpine.js | 3.14.9 | `alpine/alpine.min.js` | https://registry.npmjs.org/alpinejs/-/alpinejs-3.14.9.tgz → `dist/cdn.min.js` |
| Chart.js | 4.4.9 | `chartjs/chart.umd.min.js` | https://registry.npmjs.org/chart.js/-/chart.js-4.4.9.tgz → `dist/chart.umd.js` (already minified) |

Fonts (`static/fonts/`): Manrope 400–800 and Fraunces 600–800 + 500 italic, latin subset, woff2,
from `@fontsource/manrope@5.2.5` and `@fontsource/fraunces@5.2.5` (SIL Open Font License).

Example update without npm:

```bash
curl -L https://registry.npmjs.org/htmx.org/-/htmx.org-2.0.5.tgz | tar xz
cp package/dist/htmx.min.js static/vendor/htmx/htmx.min.js
```
-e 
Note: `//# sourceMappingURL` comments were removed from chart.umd.min.js and leaflet.js because the
source maps aren't shipped and WhiteNoise's manifest storage fails on missing references. Do the same after updating.
