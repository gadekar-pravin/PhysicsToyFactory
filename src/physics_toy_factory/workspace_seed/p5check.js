#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const vm = require("node:vm");

const MAX_SKETCH_BYTES = 100_000;
const VM_TIMEOUT_MS = 100;
const DRAW_FRAMES = 5;
const MAX_ERROR_CHARS = 500;
const MAX_CONSOLE_CHARS = 2_000;

class PolicyError extends Error {
  constructor(message) {
    super(message);
    this.name = "PolicyError";
  }
}

function bounded(value, limit = MAX_ERROR_CHARS) {
  return String(value ?? "Unknown error").replace(/[\r\n\t]+/g, " ").replace(/\s+/g, " ").slice(0, limit);
}

function fail(code, error) {
  const name = bounded(error && error.name ? error.name : "Error", 80);
  const message = bounded(error && error.message ? error.message : error);
  process.stderr.write(`P5CHECK FAIL ${name}: ${message}\n`);
  process.exit(code);
}

function validateInput() {
  if (process.argv.length !== 3 || process.argv[2] !== "sketch.js") {
    throw Object.assign(new Error("usage: node p5check.js sketch.js"), { exitCode: 2 });
  }
  let stat;
  try {
    stat = fs.lstatSync("sketch.js");
  } catch (error) {
    throw new PolicyError(`sketch.js is missing: ${bounded(error.message)}`);
  }
  if (!stat.isFile()) {
    throw new PolicyError("sketch.js must be a regular file");
  }
  if (stat.size === 0) {
    throw new PolicyError("sketch.js must not be empty");
  }
  if (stat.size > MAX_SKETCH_BYTES) {
    throw new PolicyError(`sketch.js exceeds ${MAX_SKETCH_BYTES} bytes`);
  }
  return fs.readFileSync("sketch.js", "utf8");
}

const unsupportedP5 = [
  "loadImage", "loadJSON", "loadStrings", "loadTable", "loadXML", "loadBytes", "httpGet",
  "httpPost", "httpDo", "loadSound", "createAudio", "createVideo", "createCapture", "createElement",
  "createDiv", "createP", "createSpan", "createImg", "createA", "createSlider", "createButton",
  "createCheckbox", "createSelect", "createInput", "createFileInput", "createGraphics", "WEBGL",
  "WEBGPU", "shader", "loadShader", "createShader", "save", "saveCanvas", "saveFrames"
];

const forbiddenIdentifiers = [
  "require", "module", "exports", "process", "Buffer", "fetch", "XMLHttpRequest", "WebSocket",
  "EventSource", "localStorage", "sessionStorage", "indexedDB", "document", "window", "location",
  "navigator", "Worker", "SharedWorker", "ServiceWorker", "WebAssembly", "global", "globalThis",
  "Deno", "Bun"
];

function rejectForbiddenSurface(source) {
  for (const name of unsupportedP5) {
    if (new RegExp(`\\b${name}\\b`).test(source)) {
      throw new PolicyError(`Unsupported p5 API: ${name}`);
    }
  }
  for (const name of forbiddenIdentifiers) {
    if (new RegExp(`\\b${name}\\b`).test(source)) {
      throw new PolicyError(`Forbidden identifier: ${name}`);
    }
  }
  const dynamicPatterns = [
    [/\bimport\s*\(/, "dynamic import"],
    [/\beval\s*\(/, "eval"],
    [/\b(?:AsyncFunction|GeneratorFunction|Function)\s*\(/, "dynamic Function"],
    [/\bconstructor\b/, "constructor access"],
    [/\bprototype\b|__proto__/, "prototype access"],
    [/\b(?:getPrototypeOf|setPrototypeOf|defineProperty|defineProperties)\b/, "prototype mutation"],
    [/\b(?:Proxy|Reflect)\b/, "metaprogramming"]
  ];
  for (const [pattern, label] of dynamicPatterns) {
    if (pattern.test(source)) {
      throw new PolicyError(`Forbidden dynamic-code construct: ${label}`);
    }
  }
}

function number(value, fallback = 0) {
  const converted = Number(value);
  return Number.isFinite(converted) ? converted : fallback;
}

class P5Vector {
  constructor(x = 0, y = 0) {
    this.x = number(x);
    this.y = number(y);
  }

  set(x, y) {
    if (x instanceof P5Vector) {
      this.x = x.x; this.y = x.y;
    } else {
      this.x = number(x); this.y = number(y);
    }
    return this;
  }

  copy() { return new P5Vector(this.x, this.y); }
  add(x, y) { const v = components(x, y); this.x += v.x; this.y += v.y; return this; }
  sub(x, y) { const v = components(x, y); this.x -= v.x; this.y -= v.y; return this; }
  mult(value) { const n = number(value); this.x *= n; this.y *= n; return this; }
  div(value) { const n = number(value); if (n === 0) throw new RangeError("vector division by zero"); this.x /= n; this.y /= n; return this; }
  magSq() { return this.x * this.x + this.y * this.y; }
  mag() { return Math.sqrt(this.magSq()); }
  normalize() { const m = this.mag(); return m === 0 ? this : this.div(m); }
  setMag(value) { return this.normalize().mult(value); }
  limit(value) { const maximum = Math.abs(number(value)); return this.mag() > maximum ? this.setMag(maximum) : this; }
  heading() { return Math.atan2(this.y, this.x); }
  rotate(angle) {
    const radians = number(angle); const cosine = Math.cos(radians); const sine = Math.sin(radians);
    const x = this.x * cosine - this.y * sine; this.y = this.x * sine + this.y * cosine; this.x = x;
    return this;
  }
  dot(x, y) { const v = components(x, y); return this.x * v.x + this.y * v.y; }
  dist(vector) { return P5Vector.dist(this, vector); }
  lerp(vector, amount) { this.x += (vector.x - this.x) * amount; this.y += (vector.y - this.y) * amount; return this; }

  static add(a, b) { return a.copy().add(b); }
  static sub(a, b) { return a.copy().sub(b); }
  static mult(vector, value) { return vector.copy().mult(value); }
  static div(vector, value) { return vector.copy().div(value); }
  static dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }
  static lerp(a, b, amount) { return a.copy().lerp(b, amount); }
  static fromAngle(angle, length = 1) { return new P5Vector(Math.cos(angle) * length, Math.sin(angle) * length); }
  static random2D() { const angle = deterministicRandom() * Math.PI * 2; return P5Vector.fromAngle(angle); }
}

function components(x, y) {
  return x instanceof P5Vector ? x : new P5Vector(x, y);
}

let randomState = 0x12345678;
let noiseState = 0x87654321;

function seed(value) {
  const normalized = Math.trunc(number(value, 1)) >>> 0;
  return normalized || 1;
}

function deterministicRandom() {
  randomState = (1664525 * randomState + 1013904223) >>> 0;
  return randomState / 0x100000000;
}

function createHarness() {
  const context = Object.create(null);
  const state = {
    canvasCreated: false,
    drawCalls: 0,
    shapeVertices: 0,
    consoleChars: 0,
    startedAt: 1_000,
    targetFrameRate: 60
  };

  const capture = (...values) => {
    if (state.consoleChars >= MAX_CONSOLE_CHARS) return;
    const line = bounded(values.map((value) => {
      try { return typeof value === "string" ? value : JSON.stringify(value); }
      catch { return "[unserializable]"; }
    }).join(" "), MAX_CONSOLE_CHARS - state.consoleChars);
    state.consoleChars += line.length;
  };
  context.console = Object.freeze({ log: capture, info: capture, warn: capture, error: capture, debug: capture });

  const visible = () => { state.drawCalls += 1; };
  const noOp = () => undefined;
  context.createCanvas = (width, height) => {
    const w = number(width); const h = number(height);
    if (w <= 0 || h <= 0) throw new RangeError("createCanvas dimensions must be positive");
    context.width = w; context.height = h; state.canvasCreated = true;
    return Object.freeze({ width: w, height: h });
  };
  context.resizeCanvas = (width, height) => {
    if (!state.canvasCreated) throw new Error("resizeCanvas called before createCanvas");
    context.width = number(width); context.height = number(height);
  };
  context.frameRate = (value) => {
    if (value !== undefined) state.targetFrameRate = Math.max(1, number(value, 60));
    return state.targetFrameRate;
  };
  context.millis = () => state.startedAt + context.frameCount * context.deltaTime;

  for (const name of [
    "background", "clear", "point", "line", "triangle", "quad", "rect", "square", "ellipse",
    "circle", "arc", "bezier", "curve", "text"
  ]) context[name] = visible;
  for (const name of [
    "fill", "noFill", "stroke", "noStroke", "strokeWeight", "strokeCap", "strokeJoin", "colorMode",
    "rectMode", "ellipseMode", "blendMode", "textSize", "textAlign", "push", "pop", "translate",
    "rotate", "scale", "angleMode"
  ]) context[name] = noOp;
  context.beginShape = () => { state.shapeVertices = 0; };
  context.vertex = context.curveVertex = context.bezierVertex = () => { state.shapeVertices += 1; };
  context.endShape = () => { if (state.shapeVertices > 0) visible(); state.shapeVertices = 0; };

  context.color = (r = 0, g = r, b = r, a = 255) => Object.freeze({
    r: number(r), g: number(g), b: number(b), a: number(a)
  });
  context.red = (value) => number(value && value.r);
  context.green = (value) => number(value && value.g);
  context.blue = (value) => number(value && value.b);
  context.alpha = (value) => number(value && value.a, 255);
  context.lerpColor = (a, b, amount) => context.color(
    context.lerp(a.r, b.r, amount), context.lerp(a.g, b.g, amount),
    context.lerp(a.b, b.b, amount), context.lerp(a.a, b.a, amount)
  );

  context.randomSeed = (value) => { randomState = seed(value); };
  context.random = (minimum, maximum) => {
    const unit = deterministicRandom();
    if (Array.isArray(minimum)) return minimum[Math.floor(unit * minimum.length)];
    if (minimum === undefined) return unit;
    if (maximum === undefined) return unit * number(minimum);
    return number(minimum) + unit * (number(maximum) - number(minimum));
  };
  context.noiseSeed = (value) => { noiseState = seed(value); };
  context.noiseDetail = noOp;
  context.noise = (...values) => {
    let hash = noiseState;
    for (const value of values) hash = Math.imul(hash ^ Math.trunc(number(value) * 10_000), 2654435761) >>> 0;
    return hash / 0x100000000;
  };

  Object.assign(context, {
    abs: Math.abs, ceil: Math.ceil, exp: Math.exp, floor: Math.floor, log: Math.log, max: Math.max,
    min: Math.min, pow: Math.pow, round: Math.round, sqrt: Math.sqrt, sin: Math.sin, cos: Math.cos,
    tan: Math.tan, asin: Math.asin, acos: Math.acos, atan: Math.atan, atan2: Math.atan2,
    constrain: (value, low, high) => Math.min(Math.max(value, low), high),
    dist: (x1, y1, x2, y2) => Math.hypot(x2 - x1, y2 - y1),
    lerp: (start, stop, amount) => start + (stop - start) * amount,
    mag: (x, y) => Math.hypot(x, y),
    map: (value, a1, a2, b1, b2) => b1 + ((value - a1) / (a2 - a1)) * (b2 - b1),
    norm: (value, start, stop) => (value - start) / (stop - start),
    sq: (value) => value * value,
    degrees: (radians) => radians * 180 / Math.PI,
    radians: (degrees) => degrees * Math.PI / 180,
    createVector: (x, y) => new P5Vector(x, y),
    p5: Object.freeze({ Vector: P5Vector })
  });

  Object.assign(context, {
    PI: Math.PI, TWO_PI: Math.PI * 2, HALF_PI: Math.PI / 2, QUARTER_PI: Math.PI / 4, TAU: Math.PI * 2,
    CENTER: "center", CORNER: "corner", CORNERS: "corners", RADIUS: "radius", CLOSE: "close",
    OPEN: "open", CHORD: "chord", PIE: "pie", RGB: "rgb", HSB: "hsb", HSL: "hsl",
    DEGREES: "degrees", RADIANS: "radians", ADD: "add", BLEND: "blend", ROUND: "round",
    SQUARE: "square", PROJECT: "project", MITER: "miter", BEVEL: "bevel",
    width: 100, height: 100, windowWidth: 800, windowHeight: 600, frameCount: 0, deltaTime: 1000 / 60,
    mouseX: 160, mouseY: 120, pmouseX: 152, pmouseY: 114, mouseIsPressed: false,
    key: "a", keyCode: 65, keyIsPressed: false
  });

  return { context, state };
}

function runSketch(source) {
  rejectForbiddenSurface(source);
  const { context, state } = createHarness();
  const sandbox = vm.createContext(context, {
    name: "p5check",
    codeGeneration: { strings: false, wasm: false }
  });
  const script = new vm.Script(source, { filename: "sketch.js", displayErrors: true });
  script.runInContext(sandbox, { timeout: VM_TIMEOUT_MS, displayErrors: true });

  const callable = (name) => new vm.Script(`typeof ${name} === "function"`).runInContext(sandbox, { timeout: VM_TIMEOUT_MS });
  const invoke = (name) => new vm.Script(`${name}()`, { filename: `p5check:${name}` })
    .runInContext(sandbox, { timeout: VM_TIMEOUT_MS, displayErrors: true });
  if (!callable("setup")) throw new PolicyError("setup must be a callable function");
  if (!callable("draw")) throw new PolicyError("draw must be a callable function");

  invoke("setup");
  for (let frame = 1; frame <= DRAW_FRAMES; frame += 1) {
    context.frameCount = frame;
    context.pmouseX = context.mouseX; context.pmouseY = context.mouseY;
    context.mouseX += 1; context.mouseY += 1;
    invoke("draw");
  }
  for (const callback of [
    "windowResized", "mousePressed", "mouseReleased", "mouseMoved", "mouseDragged", "keyPressed",
    "keyReleased"
  ]) {
    if (callable(callback)) invoke(callback);
  }
  if (!state.canvasCreated) throw new PolicyError("setup must call createCanvas");
  if (state.drawCalls < 1) throw new PolicyError("sketch must call at least one visible drawing primitive");
  return state;
}

try {
  const source = validateInput();
  const state = runSketch(source);
  process.stdout.write(`P5CHECK PASS frames=${DRAW_FRAMES} draw_calls=${state.drawCalls}\n`);
} catch (error) {
  fail(error && error.exitCode ? error.exitCode : 1, error);
}
