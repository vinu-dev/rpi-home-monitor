# REQ: SWR-036; RISK: RISK-012; SEC: SC-012; TEST: TC-034

# Vendored QR Library

- Library: Project Nayuki QR Code generator (TypeScript/JavaScript)
- Upstream release: `v1.8.0`
- Upstream commit: `720f62bddb7226106071d4728c292cb1df519ceb`
- Upstream source: `typescript-javascript/qrcodegen.ts`
- License: MIT

Build note:

- `qrcode.min.js` was compiled from the upstream TypeScript source with `npx -p typescript tsc --target es5 --module none --ignoreDeprecations 6.0 --outFile ...`
- The compiled JavaScript was minified with `npx terser -c -m`
- The preserved license header above the minified payload is copied verbatim from the upstream source file
