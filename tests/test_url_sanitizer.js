const assert = require("assert");
const fs = require("fs");
const path = require("path");

const templatePath = path.join(__dirname, "..", "templates", "panorama.html");
const source = fs.readFileSync(templatePath, "utf8");
const start = source.indexOf("function safeHref(");
const end = source.indexOf("function safeAnchor(", start);

assert.notStrictEqual(start, -1, "safeHref must exist in the renderer");
assert.notStrictEqual(end, -1, "safeAnchor must follow safeHref");

const safeHref = new Function(`${source.slice(start, end)}; return safeHref;`)();

assert.strictEqual(safeHref("https://example.com/a"), "https://example.com/a");
assert.strictEqual(safeHref("http://example.com/a"), "http://example.com/a");
assert.strictEqual(safeHref("./docs/design.md"), "./docs/design.md");
assert.strictEqual(safeHref("../reference.json"), "../reference.json");
assert.strictEqual(safeHref("C:\\project\\docs\\design.md"), "file:///C:/project/docs/design.md");
assert.strictEqual(safeHref("javascript:alert(1)"), null);
assert.strictEqual(safeHref("vbscript:msgbox(1)"), null);
assert.strictEqual(safeHref("data:text/html,unsafe"), null);
assert.strictEqual(safeHref("ftp://example.com/file"), null);

console.log("safeHref tests passed");
