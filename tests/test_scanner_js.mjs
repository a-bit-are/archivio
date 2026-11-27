import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const DOCS_HTML_PATH = path.join(ROOT, "docs", "scanner.html");
const DATA_HTML_PATH = path.join(ROOT, "app", "archivio", "data", "scanner.html");

function extractScannerScript() {
  const html = fs.readFileSync(DOCS_HTML_PATH, "utf8");
  const scripts = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)];
  assert.ok(scripts.length >= 2, "Expected external + inline <script> blocks in scanner.html");
  return scripts[scripts.length - 1][1];
}

function createHarness({
  search = "",
  hostname = "192.168.1.5",
  protocol = "http:",
  isSecureContext = false,
  fetchImpl = null,
} = {}) {
  const elements = new Map();
  const makeEl = (id) => {
    const classes = new Set();
    return {
      id,
      value: "",
      checked: false,
      disabled: false,
      textContent: "",
      innerHTML: "",
      style: { display: "" },
      className: "",
      classList: {
        toggle(cls, force) {
          if (force === undefined) {
            if (classes.has(cls)) classes.delete(cls);
            else classes.add(cls);
          } else if (force) {
            classes.add(cls);
          } else {
            classes.delete(cls);
          }
        },
        contains(cls) {
          return classes.has(cls);
        },
      },
      addEventListener() {},
      removeEventListener() {},
      focus() {},
      blur() {},
      appendChild() {},
      querySelectorAll() {
        return [];
      },
      contains() {
        return false;
      },
    };
  };

  const getEl = (id) => {
    if (!elements.has(id)) elements.set(id, makeEl(id));
    return elements.get(id);
  };

  const eventSources = [];
  class FakeEventSource {
    constructor(url) {
      this.url = url;
      this.readyState = 1;
      this.onmessage = null;
      this.onerror = null;
      eventSources.push(this);
    }
    close() {
      this.readyState = 2;
    }
  }

  const fetchCalls = [];
  const fakeFetch = async (url, opts = {}) => {
    fetchCalls.push({ url: String(url), opts });
    if (fetchImpl) {
      return fetchImpl(String(url), opts);
    }
    return {
      ok: true,
      status: 200,
      async json() {
        return { status: "ok" };
      },
      async text() {
        return "";
      },
    };
  };

  const fakeWindow = {
    location: { search, hostname, protocol },
    isSecureContext,
    AudioContext: null,
    webkitAudioContext: null,
    addEventListener() {},
  };

  const ctx = vm.createContext({
    window: fakeWindow,
    document: {
      getElementById: getEl,
      querySelectorAll: () => [],
      createElement: () => makeEl("created"),
      addEventListener() {},
      documentElement: makeEl("html"),
    },
    URLSearchParams,
    EventSource: FakeEventSource,
    fetch: fakeFetch,
    navigator: { vibrate: () => {} },
    AbortController,
    setTimeout,
    clearTimeout,
    Date,
    Math,
    JSON,
    Array,
    Object,
    String,
    Number,
    Boolean,
    RegExp,
    Error,
    encodeURIComponent,
    decodeURIComponent,
    console,
  });

  const code = extractScannerScript();
  vm.runInContext(code, ctx, { filename: "scanner.html" });
  const readVar = (name) => vm.runInContext(name, ctx);
  return { ctx, readVar, getEl, eventSources, fetchCalls };
}

test("docs/scanner.html and app/archivio/data/scanner.html are byte-for-byte identical", () => {
  const b1 = fs.readFileSync(DOCS_HTML_PATH);
  const b2 = fs.readFileSync(DATA_HTML_PATH);
  assert.equal(Buffer.compare(b1, b2), 0);
});

test("missing ?room= leaves ROOM empty, opens no EventSource, and shows invalid-session banner", () => {
  const { readVar, getEl, eventSources } = createHarness({
    search: "?lang=it",
    hostname: "a-bit-are.github.io",
    protocol: "https:",
    isSecureContext: true,
  });
  assert.equal(readVar("ROOM"), "");
  assert.equal(eventSources.length, 0);
  assert.equal(getEl("alertDup").style.display, "block");
  assert.match(getEl("alertDup").innerHTML, /Sessione non valida/);
});

test("malicious ?room= values are rejected", () => {
  for (const bad of ["../../evil", "<script>alert(1)</script>", "abc", "ROOM WITH SPACE"]) {
    const { readVar, eventSources } = createHarness({
      search: `?room=${encodeURIComponent(bad)}`,
      hostname: "a-bit-are.github.io",
    });
    assert.equal(readVar("ROOM"), "");
    assert.equal(eventSources.length, 0);
  }
});

test("invalid ?cat= defaults to Libri and setCategory ignores invalid categories", () => {
  const { ctx, readVar } = createHarness({
    search: `?room=ARCH-ABCD-2345&cat=${encodeURIComponent("<img src=x onerror=1>")}`,
  });
  assert.equal(readVar("CURRENT_CAT"), "Libri");
  ctx.setCategory("InvalidCat", false);
  assert.equal(readVar("CURRENT_CAT"), "Libri");
  ctx.setCategory("CD", false);
  assert.equal(readVar("CURRENT_CAT"), "CD");
});

test("DOM XSS payloads in code, publisher, and category are HTML-escaped", () => {
  const { ctx, getEl } = createHarness({
    search: "?room=ARCH-ABCD-2345&lang=it",
  });

  ctx.handleMacResponse({
    status: "issn_warning",
    code: "<img src=x onerror=alert(1)>",
  });
  assert.ok(!getEl("alertIssn").innerHTML.includes("<img"));
  assert.ok(getEl("alertIssn").innerHTML.includes("&lt;img"));

  ctx.selectCandidateItem({
    title: "Test Book",
    author: "Test Author",
    publisher: "<svg onload=alert(1)>",
    year: "2020",
  });
  assert.ok(!getEl("alertOk").innerHTML.includes("<svg"));
  assert.ok(getEl("alertOk").innerHTML.includes("&lt;svg"));

  vm.runInContext('CURRENT_CAT = "<script>evil()</script>";', ctx);
  ctx.handleMacResponse({ req_kind: "sync_preview", is_duplicate: true });
  assert.ok(!getEl("alertDup").innerHTML.includes("<script>"));
  assert.ok(getEl("alertDup").innerHTML.includes("&lt;script&gt;"));
});

test("local Wi-Fi mode does not open EventSource on load and never leaks to ntfy.sh on local fetch error", async () => {
  const { ctx, getEl, eventSources, fetchCalls } = createHarness({
    search: "?room=ARCH-ABCD-2345&lang=it",
    hostname: "192.168.1.5",
    protocol: "http:",
    fetchImpl: async () => {
      throw new Error("Network timeout");
    },
  });
  assert.equal(eventSources.length, 0);
  await ctx.sendAction({ action: "set_category", category: "CD" });
  assert.equal(fetchCalls.length, 1);
  assert.equal(fetchCalls[0].url, "/api/action");
  assert.ok(!fetchCalls.some((c) => c.url.includes("ntfy.sh")));
  assert.equal(getEl("alertDup").style.display, "block");
  assert.match(getEl("alertDup").innerHTML, /Errore di connessione locale/);
});

test("static host returning 404 switches DIRECT_API_AVAILABLE off, opens EventSource, and uses ntfy.sh", async () => {
  const { ctx, readVar, eventSources, fetchCalls } = createHarness({
    search: "?room=ARCH-ABCD-2345&lang=it",
    hostname: "scanner.example.com",
    protocol: "https:",
    isSecureContext: true,
    fetchImpl: async (url) => {
      if (url === "/api/action") {
        return { ok: false, status: 404, async json() { return {}; } };
      }
      return { ok: true, status: 200, async json() { return {}; } };
    },
  });
  await ctx.sendAction({ action: "set_category", category: "Vinili" });
  assert.equal(readVar("DIRECT_API_AVAILABLE"), false);
  assert.equal(eventSources.length, 1);
  assert.equal(eventSources[0].url, "https://ntfy.sh/archivio_res_ARCH-ABCD-2345/sse");
  assert.equal(fetchCalls[1].url, "https://ntfy.sh/archivio_req_ARCH-ABCD-2345");
});

test("GitHub Pages mode subscribes to EventSource and ignores non-ntfy attachment URLs", async () => {
  const { eventSources, fetchCalls } = createHarness({
    search: "?room=ARCH-ABCD-2345&lang=en",
    hostname: "a-bit-are.github.io",
    protocol: "https:",
    isSecureContext: true,
  });
  assert.equal(eventSources.length, 1);
  assert.equal(eventSources[0].url, "https://ntfy.sh/archivio_res_ARCH-ABCD-2345/sse");

  await eventSources[0].onmessage({
    data: JSON.stringify({
      message: "",
      attachment: { url: "https://evil.example/payload.json" },
    }),
  });
  assert.equal(fetchCalls.length, 0);
});
