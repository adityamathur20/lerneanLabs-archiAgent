# dwg2dxf

DWG in, ASCII DXF out, through [LibreDWG](https://www.gnu.org/software/libredwg/)
compiled to WebAssembly (`@mlightcad/libredwg-web`).

```bash
npm ci                                   # once, in this directory
node convert.mjs plan.dwg plan.dxf       # 0 converted, 1 failed, 2 bad usage
```

## Licence: this is a separate program

LibreDWG is **GPL-3.0-or-later**, so this directory is too (`COPYING`).

archiAgent is not GPL and must not become so. The boundary that keeps it that
way:

- archiAgent runs `node convert.mjs` as a **subprocess** and reads the DXF file
  it writes. It never imports, links or bundles anything from here.
- Nothing from this directory goes to a browser. The archiViewer CI guard fails
  the build if `@mlightcad/libredwg-*` appears in the viewer's lockfiles or
  `dist/`.
- Running it on our own servers is not distribution. **Handing someone the
  worker image** (customer self-hosting, a public registry) is, and then this
  program's source must be offered with it under GPL-3. The image is on
  private GHCR for that reason.

## Why it is shaped like this

- **One file per process.** LibreDWG is C compiled to WASM. A drawing that
  corrupts its heap, or a leak across conversions, dies with the process.
- **`--liftoff-only`** (set inside the script). V8's optimising compiler spends
  ~7 s on the 9.5 MB module, and the process waits for it on exit. Baseline
  compilation brings a whole conversion to ~0.3 s with byte-identical output
  (measured 2026-10-07).
- **Written to a temporary name, then renamed**, so no reader ever sees a
  half-written DXF and a failure never leaves one behind.
- **The WASM loads from `node_modules` here**, never from a CDN.

## Known limits

Measured by `scripts/gates/dwg_fidelity.py` on LibreDWG's public samples
(`docs/superpowers/notes/2026-10-07-libredwg-fidelity-gate.md`):

- 17 of 18 samples (R14 through 2018) convert, and ezdxf reads every result in
  strict mode.
- `example_2004.dwg` fails in LibreDWG's **DXF writer** ("table index is out of
  bounds") although its parser reads the same file (67 entities).
