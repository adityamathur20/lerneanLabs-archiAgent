#!/usr/bin/env node
/**
 * dwg2dxf — DWG in, ASCII DXF out, through LibreDWG compiled to WebAssembly.
 *
 *   node convert.mjs <in.dwg> <out.dxf>
 *
 * This is a SEPARATE PROGRAM, licensed GPL-3.0-or-later because LibreDWG is.
 * archiAgent runs it as a subprocess and reads the file it writes; it never
 * imports or links it. Keep it that way: see README.md.
 *
 * Exit codes: 0 converted, 1 conversion failed, 2 bad usage.
 *
 * One file per process on purpose. LibreDWG is C compiled to WASM: a drawing
 * that corrupts its heap, or a leak across conversions, dies with this process
 * instead of outliving the job that caused it.
 */
import { readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import v8 from "node:v8";

// Baseline WASM compilation only. LibreDWG is a 9.5 MB module and this process
// converts one file and exits, so V8's optimising tier is pure cost: measured
// on 2026-10-07 it added ~7 s of background compilation that the exit then
// waited for, against ~0.3 s for the whole run without it. Output is
// byte-identical. Must run before the module is compiled.
v8.setFlagsFromString("--liftoff-only");

const EXIT_OK = 0;
const EXIT_FAILED = 1;
const EXIT_USAGE = 2;

const here = path.dirname(fileURLToPath(import.meta.url));
const packageDir = path.join(here, "node_modules", "@mlightcad", "libredwg-web");

function fail(code, message) {
  process.stderr.write(`dwg2dxf: ${message}\n`);
  process.exit(code);
}

const [input, output, ...extra] = process.argv.slice(2);
if (!input || !output || extra.length) {
  fail(EXIT_USAGE, "usage: node convert.mjs <in.dwg> <out.dxf>");
}

let bytes;
try {
  bytes = readFileSync(input);
} catch (error) {
  fail(EXIT_FAILED, `cannot read ${input}: ${error.message}`);
}
if (bytes.length === 0) fail(EXIT_FAILED, `${input} is empty`);

// The WASM is loaded from this directory's own node_modules, never fetched:
// a conversion must not depend on a registry or a CDN being reachable.
let LibreDwg;
try {
  ({ LibreDwg } = await import(path.join(packageDir, "dist", "libredwg-web.js")));
} catch (error) {
  fail(EXIT_FAILED, `LibreDWG is not installed here (run npm ci in ${here}): ${error.message}`);
}

let dxf;
try {
  const libredwg = await LibreDwg.create(path.join(packageDir, "wasm") + path.sep);
  // A copy, not `bytes.buffer`: a small Buffer is a view into Node's shared
  // pool, and the whole pool would be handed to the parser as the file.
  const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
  dxf = libredwg.dwg_write_dxf(buffer);
} catch (error) {
  fail(EXIT_FAILED, `LibreDWG could not read ${path.basename(input)}: ${error?.message ?? error}`);
}
if (!dxf || dxf.length === 0) {
  fail(EXIT_FAILED, `LibreDWG produced no DXF for ${path.basename(input)}`);
}

// Written beside the target and renamed into place, so a reader never sees a
// half-written DXF and a failure never leaves one behind.
const partial = `${output}.partial-${process.pid}`;
try {
  writeFileSync(partial, dxf);
  renameSync(partial, output);
} catch (error) {
  rmSync(partial, { force: true });
  fail(EXIT_FAILED, `cannot write ${output}: ${error.message}`);
}
process.exit(EXIT_OK);
