# Bundled Swagger UI

Source: [swagger-api/swagger-ui v5.33.1](https://github.com/swagger-api/swagger-ui/releases/tag/v5.33.1),
the upstream `dist/swagger-ui-bundle.js` and `dist/swagger-ui.css` files.
Apache-2.0 and bundled dependency notices are included alongside the assets.

The files use deterministic gzip (`mtime=0`) to keep the deployment image small.
`manifest.json` records upstream and packaged SHA-256 hashes. The handler serves
gzip to supporting clients and decompresses for other clients; no CDN is needed.

Swagger uses the protected local OpenAPI artifact. Authorization persistence and
the external validator are disabled using the upstream
[configuration options](https://swagger.io/docs/open-source-tools/swagger-ui/usage/configuration/).
No credentials are embedded in these assets or the generated HTML/schema.
