// REQ: SWR-036; RISK: RISK-012; SEC: SC-012; TEST: TC-034
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const TEMPLATE_PATH = path.resolve(
  __dirname,
  "../../camera_streamer/templates/setup.html",
);
const VENDOR_PATH = path.resolve(
  __dirname,
  "../../camera_streamer/templates/vendor/qrcode.min.js",
);

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function stripTags(value) {
  return String(value).replace(/<[^>]*>/g, "");
}

function createElement(id) {
  const attributes = new Map();
  const element = {
    id,
    className: "",
    disabled: false,
    value: "",
    style: { display: "" },
    focus() {},
    setAttribute(name, value) {
      attributes.set(name, String(value));
    },
    getAttribute(name) {
      return attributes.has(name) ? attributes.get(name) : "";
    },
  };

  let textContent = "";
  let innerHTML = "";

  Object.defineProperty(element, "textContent", {
    get() {
      return textContent;
    },
    set(value) {
      textContent = value == null ? "" : String(value);
      innerHTML = escapeHtml(textContent);
    },
  });

  Object.defineProperty(element, "innerHTML", {
    get() {
      return innerHTML;
    },
    set(value) {
      innerHTML = value == null ? "" : String(value);
      textContent = stripTags(innerHTML);
    },
  });

  return element;
}

function createDocument() {
  const ids = [
    "view-form",
    "view-connecting",
    "view-result",
    "cam-address-display",
    "cam-address-qr",
    "cam-login-user",
    "in-admin-user",
    "net-list-wrap",
    "net-list",
    "in-ssid",
    "in-pass",
    "in-server",
    "in-port",
    "in-admin-pw",
    "result-msg",
    "result-address-card",
    "result-address-display",
    "result-address-qr",
    "result-next-steps",
    "btn-retry",
    "btn-save",
  ];
  const elements = new Map(ids.map((id) => [id, createElement(id)]));

  elements.get("view-form").className = "active";
  elements.get("net-list-wrap").style.display = "none";
  elements.get("result-address-card").style.display = "none";
  elements.get("result-next-steps").style.display = "none";
  elements.get("btn-retry").style.display = "none";
  elements.get("in-admin-user").value = "admin";
  elements.get("cam-address-qr").textContent = "Resolving IP...";
  elements.get("result-address-qr").textContent = "Resolving IP...";

  return {
    getElementById(id) {
      if (!elements.has(id)) {
        elements.set(id, createElement(id));
      }
      return elements.get(id);
    },
    createElement() {
      return createElement("");
    },
  };
}

function buildContext(options) {
  const document = createDocument();
  const warnings = [];
  const fetchCalls = [];
  const statusQueue = (options.statusSequence || [options.statusPayload]).map(
    (entry) => JSON.parse(JSON.stringify(entry)),
  );

  function currentStatusPayload() {
    if (!statusQueue.length) {
      return JSON.parse(JSON.stringify(options.statusPayload));
    }
    if (statusQueue.length === 1) {
      return JSON.parse(JSON.stringify(statusQueue[0]));
    }
    return JSON.parse(JSON.stringify(statusQueue.shift()));
  }

  function fetch(url, requestOptions) {
    fetchCalls.push({ url, requestOptions: requestOptions || null });
    if (url === "/api/status") {
      return Promise.resolve({
        json: () => Promise.resolve(currentStatusPayload()),
      });
    }
    if (url === "/api/networks" || url === "/api/rescan") {
      return Promise.resolve({
        json: () => Promise.resolve({ networks: [] }),
      });
    }
    if (url === "/api/connect") {
      return Promise.resolve({
        json: () =>
          Promise.resolve(
            options.connectResponse || { hostname: options.hostname || "cam-test" },
          ),
      });
    }
    return Promise.reject(new Error(`Unexpected fetch URL: ${url}`));
  }

  const context = {
    alert() {},
    clearTimeout() {},
    console: {
      log() {},
      warn(...args) {
        warnings.push(args.map(String).join(" "));
      },
      error(...args) {
        warnings.push(args.map(String).join(" "));
      },
    },
    document,
    fetch,
    Function(source) {
      return function runInHarnessContext() {
        return vm.runInContext(source, context, {
          filename: "vendored-qrcode.js",
        });
      };
    },
    Promise,
    setTimeout(fn) {
      fn();
      return 1;
    },
  };

  context.window = context;
  context.globalThis = context;
  context.fetchCalls = fetchCalls;
  context.warnings = warnings;
  return context;
}

function loadSetupScript(options) {
  const template = fs
    .readFileSync(TEMPLATE_PATH, "utf8")
    .replace(/{{CAMERA_ID}}/g, "cam-test-001")
    .replace(/{{HOSTNAME}}/g, options.hostname || "cam-test")
    .replace(/{{QRCODE_LIB}}/g, JSON.stringify(options.librarySource));
  const match = template.match(/<script>([\s\S]*?)<\/script>/);
  assert.ok(match, "setup.html script block not found");

  const context = buildContext(options);
  vm.createContext(context);
  vm.runInContext(match[1], context, { filename: "setup.html" });
  return context;
}

async function flushPromises(rounds = 6) {
  for (let index = 0; index < rounds; index += 1) {
    await Promise.resolve();
  }
}

function extractRenderedModules(slotHtml, border = 2) {
  const pathMatch = slotHtml.match(/<path d="([^"]+)"/);
  assert.ok(pathMatch, "rendered QR path missing");
  const moduleSet = new Set();
  const regex = /M(\d+),(\d+)h1v1h-1z/g;
  let match;
  while ((match = regex.exec(pathMatch[1])) !== null) {
    moduleSet.add(`${Number(match[1]) - border},${Number(match[2]) - border}`);
  }
  return moduleSet;
}

function expectedModules(context, targetUrl) {
  const qr = context.qrcodegen.QrCode.encodeText(
    targetUrl,
    context.qrcodegen.QrCode.Ecc.MEDIUM,
  );
  const moduleSet = new Set();
  for (let y = 0; y < qr.size; y += 1) {
    for (let x = 0; x < qr.size; x += 1) {
      if (qr.getModule(x, y)) {
        moduleSet.add(`${x},${y}`);
      }
    }
  }
  return moduleSet;
}

function assertModuleSetsEqual(actual, expected) {
  assert.equal(actual.size, expected.size, "QR module count mismatch");
  for (const module of expected) {
    assert.ok(actual.has(module), `missing QR module ${module}`);
  }
}

async function runScenario(name) {
  const vendorSource = fs.readFileSync(VENDOR_PATH, "utf8");

  if (name === "setup-complete") {
    const context = loadSetupScript({
      hostname: "cam-test",
      librarySource: vendorSource,
      statusPayload: {
        status: "connected",
        error: "",
        setup_complete: true,
        camera_id: "cam-test-001",
        hostname: "cam-test",
        ip_address: "192.168.1.42",
      },
    });
    await flushPromises();

    const resultView = context.document.getElementById("view-result");
    const resultSlot = context.document.getElementById("result-address-qr");
    const connectingSlot = context.document.getElementById("cam-address-qr");
    const targetUrl = "https://192.168.1.42:443";

    assert.equal(resultView.className, "active");
    assert.equal(
      context.document.getElementById("result-msg").textContent,
      "Setup complete! Camera is on your home WiFi.",
    );
    assert.equal(resultSlot.getAttribute("data-target-url"), targetUrl);
    assert.equal(connectingSlot.getAttribute("data-target-url"), targetUrl);
    assert.match(resultSlot.innerHTML, /<svg/);
    assert.match(
      context.document.getElementById("result-address-display").innerHTML,
      /https:\/\/cam-test\.local/,
    );
    assertModuleSetsEqual(
      extractRenderedModules(resultSlot.innerHTML),
      expectedModules(context, targetUrl),
    );
    return { scenario: name, ok: true };
  }

  if (name === "connected-poll") {
    const context = loadSetupScript({
      hostname: "cam-test",
      librarySource: vendorSource,
      statusPayload: {
        status: "idle",
        error: "",
        setup_complete: false,
        camera_id: "cam-test-001",
        hostname: "cam-test",
        ip_address: "",
      },
    });
    await flushPromises();

    context.showConnecting("cam-test");
    context.renderQrIfPossible("10.0.0.15");

    const connectingView = context.document.getElementById("view-connecting");
    const connectingSlot = context.document.getElementById("cam-address-qr");
    assert.equal(connectingView.className, "active");
    assert.equal(
      connectingSlot.getAttribute("data-target-url"),
      "https://10.0.0.15:443",
    );
    assert.match(connectingSlot.innerHTML, /<svg/);
    return { scenario: name, ok: true };
  }

  if (name === "idempotent-replace") {
    const context = loadSetupScript({
      hostname: "cam-test",
      librarySource: vendorSource,
      statusPayload: {
        status: "idle",
        error: "",
        setup_complete: false,
        camera_id: "cam-test-001",
        hostname: "cam-test",
        ip_address: "",
      },
    });
    await flushPromises();

    const slot = context.document.getElementById("cam-address-qr");
    context.renderQrIfPossible("10.0.0.1");
    const firstHtml = slot.innerHTML;
    context.renderQrIfPossible("10.0.0.1");
    assert.equal(slot.innerHTML, firstHtml);
    assert.equal(slot.getAttribute("data-target-url"), "https://10.0.0.1:443");

    context.renderQrIfPossible("10.0.0.2");
    assert.notEqual(slot.innerHTML, firstHtml);
    assert.equal(slot.getAttribute("data-target-url"), "https://10.0.0.2:443");
    return { scenario: name, ok: true };
  }

  if (name === "broken-library") {
    const context = loadSetupScript({
      hostname: "cam-test",
      librarySource: "window.qrcodegen = {",
      statusPayload: {
        status: "connected",
        error: "",
        setup_complete: true,
        camera_id: "cam-test-001",
        hostname: "cam-test",
        ip_address: "192.168.1.42",
      },
    });
    await flushPromises();

    const resultSlot = context.document.getElementById("result-address-qr");
    assert.equal(
      context.document.getElementById("result-msg").textContent,
      "Setup complete! Camera is on your home WiFi.",
    );
    assert.equal(resultSlot.getAttribute("data-target-url"), "");
    assert.equal(resultSlot.textContent, "Resolving IP...");
    assert.ok(
      context.warnings.some((entry) => entry.includes("QR library failed to load")),
      "expected broken-library warning",
    );
    return { scenario: name, ok: true };
  }

  throw new Error(`Unknown scenario: ${name}`);
}

runScenario(process.argv[2])
  .then((result) => {
    process.stdout.write(`${JSON.stringify(result)}\n`);
  })
  .catch((error) => {
    process.stderr.write(`${error.stack || error.message}\n`);
    process.exitCode = 1;
  });
