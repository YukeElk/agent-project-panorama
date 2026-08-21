"use strict";

const fs = require("fs");
const path = require("path");

const template = fs.readFileSync(
  path.join(__dirname, "..", "templates", "panorama-multi-view.html"),
  "utf8"
);
const scripts = [...template.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(
  (match) => match[1]
);
if (scripts.length !== 2) throw new Error(`expected 2 script blocks, got ${scripts.length}`);
new Function(scripts[1]);
for (const required of [
  "data-view",
  "data-node",
  "data-edge",
  "hashchange",
  "onwheel",
  "onpointerdown",
  "evidencePins",
  "__PANORAMA_RENDERER_QA__",
  "edgeThroughNodes",
  "fitView",
  "data-relation-action",
  "fact-filter",
  "trace-mode",
  "relation",
]) {
  if (!scripts[1].includes(required)) throw new Error(`missing renderer behavior: ${required}`);
}
if (/https?:\/\//.test(template)) throw new Error("renderer template must not contain network URLs");
console.log("multi-view renderer syntax tests passed");
