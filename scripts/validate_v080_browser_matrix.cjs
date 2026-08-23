"use strict";

// Reproducible V0.8 viewport, input-device, reduced-motion, and print validation.

const fs = require("node:fs");
const crypto = require("node:crypto");
const path = require("node:path");
const { chromium } = require("playwright");

const viewports = [
  { width: 1440, height: 900 },
  { width: 1600, height: 1000 },
  { width: 1920, height: 1080 },
  { width: 2048, height: 1320 },
  { width: 839, height: 1000 },
  { width: 430, height: 900 },
  { width: 360, height: 800 },
];

function usage() {
  console.error(
    "Usage: node validate_v080_browser_matrix.cjs <url> <matrix-output.json> [browser-measurement-output.json]"
  );
  process.exit(2);
}

async function inspect(page, viewport, screenshotPath) {
  const browserMessages = [];
  page.on("console", (message) => {
    if (["error", "warning"].includes(message.type())) {
      browserMessages.push(`${message.type()}: ${message.text()}`);
    }
  });
  page.on("pageerror", (error) => browserMessages.push(`pageerror: ${error.message}`));
  await page.goto(process.argv[2], { waitUntil: "load" });
  await page.getByRole("button", { name: "系统", exact: true }).click();
  await page.waitForTimeout(100);
  const viewIds = await page
    .locator("panorama-guided-v08 .lens[data-view-id]")
    .evaluateAll((nodes) => nodes.map((node) => node.dataset.viewId));
  const inspectScene = () =>
    page.evaluate(() => {
      const host = document.querySelector("panorama-guided-v08");
      const shadow = host && host.shadowRoot;
      const canvas = shadow && shadow.querySelector(".canvas-viewport");
      const nodes = shadow ? [...shadow.querySelectorAll(".node")] : [];
      const edges = shadow ? [...shadow.querySelectorAll(".edge")] : [];
      const canvasRect = canvas && canvas.getBoundingClientRect();
      const nodeRects = nodes.map((node) => {
        const rect = node.getBoundingClientRect();
        return { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom };
      });
      let nodeOverlapCount = 0;
      for (let left = 0; left < nodeRects.length; left += 1) {
        for (let right = left + 1; right < nodeRects.length; right += 1) {
          const a = nodeRects[left];
          const b = nodeRects[right];
          if (
            Math.min(a.right, b.right) - Math.max(a.left, b.left) > 2 &&
            Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 2
          ) {
            nodeOverlapCount += 1;
          }
        }
      }
      const sideOverflowCount = canvasRect
        ? nodeRects.filter(
            (rect) =>
              rect.left < canvasRect.left - 2 ||
              rect.right > canvasRect.right + 2 ||
              rect.top < canvasRect.top - 2 ||
              rect.bottom > canvasRect.bottom + 2
          ).length
        : nodeRects.length;
      let edgeThroughNodeCount = 0;
      edges.forEach((edge) => {
        const length = edge.getTotalLength();
        const matrix = edge.getScreenCTM();
        if (!matrix || !length) return;
        let intersects = false;
        for (let step = 2; step <= 18 && !intersects; step += 1) {
          const point = edge.getPointAtLength((length * step) / 20);
          const screen = new DOMPoint(point.x, point.y).matrixTransform(matrix);
          intersects = nodeRects.some(
            (rect) =>
              screen.x > rect.left + 2 &&
              screen.x < rect.right - 2 &&
              screen.y > rect.top + 2 &&
              screen.y < rect.bottom - 2
          );
        }
        if (intersects) edgeThroughNodeCount += 1;
      });
      const title = shadow && shadow.querySelector(".view-head h2");
      return {
        title: title ? title.textContent.trim() : "unknown",
        nodeCount: nodes.length,
        edgeCount: edges.length,
        nodeOverlapCount,
        edgeThroughNodeCount,
        sideOverflowCount,
      };
    });
  const scenes = [];
  for (const viewId of viewIds) {
    await page
      .locator(`panorama-guided-v08 .lens[data-view-id="${viewId}"]`)
      .click();
    await page.waitForTimeout(40);
    scenes.push(await inspectScene());
  }
  if (viewIds.length) {
    await page
      .locator(`panorama-guided-v08 .lens[data-view-id="${viewIds[0]}"]`)
      .click();
    const orchestrator = page
      .locator("panorama-guided-v08 .node")
      .filter({ hasText: "Orchestrator" })
      .first();
    if (await orchestrator.count()) {
      await orchestrator.dblclick();
      await page.waitForTimeout(40);
      scenes.push(await inspectScene());
      await page.locator("panorama-guided-v08 [data-nav-back]").click();
    }
  }
  const metrics = await page.evaluate(() => {
    const host = document.querySelector("panorama-guided-v08");
    const shadow = host && host.shadowRoot;
    const canvas = shadow && shadow.querySelector(".canvas-viewport");
    const world = shadow && shadow.querySelector(".canvas-world");
    const stage = shadow && shadow.querySelector(".stage");
    const rail = shadow && shadow.querySelector(".rail");
    const doc = document.documentElement;
    const rect = (node) => {
      if (!node) return null;
      const value = node.getBoundingClientRect();
      return {
        left: Math.round(value.left),
        top: Math.round(value.top),
        right: Math.round(value.right),
        bottom: Math.round(value.bottom),
        width: Math.round(value.width),
        height: Math.round(value.height),
      };
    };
    const canvasStyle = canvas ? getComputedStyle(canvas) : null;
    return {
      innerWidth: window.innerWidth,
      innerHeight: window.innerHeight,
      documentScrollWidth: doc.scrollWidth,
      documentScrollHeight: doc.scrollHeight,
      horizontalDocumentOverflow: doc.scrollWidth > window.innerWidth + 1,
      canvasWorldCount: shadow
        ? shadow.querySelectorAll('[data-canvas-world="one"]').length
        : 0,
      canvasRuntime: canvas && canvas.getAttribute("data-canvas-runtime"),
      canvasOverflowX: canvasStyle && canvasStyle.overflowX,
      canvasTouchAction: canvasStyle && canvasStyle.touchAction,
      stage: rect(stage),
      rail: rect(rail),
      canvas: rect(canvas),
      world: rect(world),
    };
  });
  metrics.scenes = scenes;
  metrics.nodeOverlapCount = Math.max(0, ...scenes.map((item) => item.nodeOverlapCount));
  metrics.edgeThroughNodeCount = Math.max(
    0,
    ...scenes.map((item) => item.edgeThroughNodeCount)
  );
  metrics.sideOverflowCount = Math.max(0, ...scenes.map((item) => item.sideOverflowCount));
  const screenshot = await page.screenshot({ path: screenshotPath, fullPage: false });
  metrics.screenshotSha256 = crypto.createHash("sha256").update(screenshot).digest("hex");
  metrics.screenshotBytes = screenshot.length;
  const failures = [];
  if (metrics.horizontalDocumentOverflow) failures.push("horizontal_document_overflow");
  if (viewport.width >= 1440 && metrics.documentScrollHeight > metrics.innerHeight + 1) {
    failures.push("desktop_vertical_document_overflow");
  }
  if (metrics.canvasWorldCount !== 1) failures.push("canvas_world_count_not_one");
  if (metrics.canvasRuntime !== "unified") failures.push("canvas_runtime_not_unified");
  if (metrics.canvasOverflowX !== "hidden") failures.push("canvas_overflow_not_hidden");
  if (!metrics.canvas || metrics.canvas.width < 280 || metrics.canvas.height < 360) {
    failures.push("canvas_too_small");
  }
  if (metrics.nodeOverlapCount) failures.push("node_overlap");
  if (metrics.edgeThroughNodeCount) failures.push("edge_through_node");
  if (metrics.sideOverflowCount) failures.push("node_side_overflow");
  if (browserMessages.length) failures.push("browser_console_messages");
  return { viewport, metrics, browserMessages, failures, status: failures.length ? "failed" : "passed" };
}

async function main() {
  if (process.argv.length !== 4 && process.argv.length !== 5) usage();
  const executablePath = process.env.PANORAMA_BROWSER_EXECUTABLE || undefined;
  const measurementOutput = process.argv[4] || null;
  const screenshotDirectory = path.join(
    path.dirname(measurementOutput || process.argv[3]),
    "screenshots"
  );
  fs.mkdirSync(screenshotDirectory, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath });
  const results = [];
  try {
    for (const viewport of viewports) {
      const context = await browser.newContext({ viewport, hasTouch: viewport.width <= 839 });
      const page = await context.newPage();
      results.push(
        await inspect(
          page,
          viewport,
          path.join(screenshotDirectory, `${viewport.width}x${viewport.height}.png`)
        )
      );
      await context.close();
    }

    const interactionContext = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      hasTouch: true,
    });
    const interactionPage = await interactionContext.newPage();
    await interactionPage.goto(process.argv[2], { waitUntil: "load" });
    await interactionPage.getByRole("button", { name: "系统", exact: true }).click();
    const canvas = interactionPage.locator("panorama-guided-v08 .canvas-viewport");
    const drillableNode = interactionPage
      .locator("panorama-guided-v08 .node")
      .filter({ hasText: "Orchestrator" })
      .first();
    const transform = () =>
      interactionPage.locator("panorama-guided-v08 .canvas-world").evaluate(
        (node) => getComputedStyle(node).transform
      );
    const canvasBox = await canvas.boundingBox();
    if (!canvasBox) throw new Error("Unified canvas has no bounding box");

    const beforeMousePan = await transform();
    await interactionPage.mouse.move(
      canvasBox.x + canvasBox.width / 2,
      canvasBox.y + canvasBox.height - 80
    );
    await interactionPage.mouse.down();
    await interactionPage.mouse.move(
      canvasBox.x + canvasBox.width / 2 + 42,
      canvasBox.y + canvasBox.height - 46,
      { steps: 4 }
    );
    await interactionPage.mouse.up();
    const afterMousePan = await transform();

    const beforeWheel = await transform();
    await interactionPage.mouse.move(
      canvasBox.x + canvasBox.width / 2,
      canvasBox.y + canvasBox.height / 2
    );
    await interactionPage.mouse.wheel(0, -240);
    await interactionPage.waitForTimeout(50);
    const afterWheel = await transform();

    await drillableNode.focus();
    await drillableNode.press("Enter");
    const keyboardDrilledIntoChild =
      (await interactionPage
        .locator('panorama-guided-v08 .breadcrumb [aria-current="page"]')
        .textContent())?.includes("内部逻辑") === true;

    const beforeTouchPan = await transform();
    const cdp = await interactionContext.newCDPSession(interactionPage);
    const touchStart = {
      x: Math.round(canvasBox.x + canvasBox.width / 2),
      y: Math.round(canvasBox.y + canvasBox.height - 100),
    };
    await cdp.send("Input.dispatchTouchEvent", {
      type: "touchStart",
      touchPoints: [{ ...touchStart, id: 1 }],
    });
    await cdp.send("Input.dispatchTouchEvent", {
      type: "touchMove",
      touchPoints: [{ x: touchStart.x - 36, y: touchStart.y - 28, id: 1 }],
    });
    await cdp.send("Input.dispatchTouchEvent", {
      type: "touchEnd",
      touchPoints: [],
    });
    await interactionPage.waitForTimeout(50);
    const afterTouchPan = await transform();
    const interaction = {
      mousePanChanged: beforeMousePan !== afterMousePan,
      wheelZoomChanged: beforeWheel !== afterWheel,
      keyboardDrilledIntoChild,
      touchPanChanged: beforeTouchPan !== afterTouchPan,
    };
    await interactionContext.close();

    const reducedContext = await browser.newContext({
      viewport: { width: 430, height: 900 },
      reducedMotion: "reduce",
      hasTouch: true,
    });
    const reducedPage = await reducedContext.newPage();
    await reducedPage.goto(process.argv[2], { waitUntil: "load" });
    await reducedPage.getByRole("button", { name: "系统", exact: true }).click();
    const reducedMotion = await reducedPage.evaluate(() => {
      const host = document.querySelector("panorama-guided-v08");
      const shadow = host && host.shadowRoot;
      const node = shadow && shadow.querySelector(".node");
      const style = node && getComputedStyle(node);
      return {
        mediaMatches: matchMedia("(prefers-reduced-motion: reduce)").matches,
        animationDuration: style && style.animationDuration,
        transitionDuration: style && style.transitionDuration,
      };
    });
    await reducedContext.close();

    const printContext = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const printPage = await printContext.newPage();
    await printPage.goto(process.argv[2], { waitUntil: "load" });
    await printPage.getByRole("button", { name: "系统", exact: true }).click();
    await printPage.emulateMedia({ media: "print" });
    const printMode = await printPage.evaluate(() => {
      const host = document.querySelector("panorama-guided-v08");
      const shadow = host && host.shadowRoot;
      const top = shadow && shadow.querySelector(".top");
      const edge = shadow && shadow.querySelector(".edge");
      return {
        topDisplay: top && getComputedStyle(top).display,
        edgeAnimation: edge && getComputedStyle(edge).animationName,
      };
    });
    await printContext.close();

    const payload = {
      formatVersion: "panorama-v080-browser-matrix.v0.1",
      url: process.argv[2],
      generatedAt: new Date().toISOString(),
      browser: "chromium",
      results,
      interaction,
      reducedMotion,
      printMode,
      status:
        results.every((item) => item.status === "passed") &&
        Object.values(interaction).every(Boolean) &&
        reducedMotion.mediaMatches &&
        printMode.topDisplay === "none"
          ? "passed"
          : "failed",
    };
    fs.mkdirSync(path.dirname(process.argv[3]), { recursive: true });
    fs.writeFileSync(process.argv[3], JSON.stringify(payload, null, 2) + "\n", "utf8");
    if (measurementOutput) {
      const browserMessages = results.flatMap((item) => item.browserMessages);
      const measurement = {
        viewports: results.map((item) => ({
          width: item.viewport.width,
          height: item.viewport.height,
          documentOverflowX: item.metrics.horizontalDocumentOverflow,
          documentOverflowY: item.metrics.documentScrollHeight > item.metrics.innerHeight + 1,
          nodeOverlapCount: item.metrics.nodeOverlapCount,
          edgeThroughNodeCount: item.metrics.edgeThroughNodeCount,
          sideOverflowCount: item.metrics.sideOverflowCount,
          captured: true,
          screenshotSha256: item.metrics.screenshotSha256,
          screenshotBytes: item.metrics.screenshotBytes,
        })),
        console: {
          errorCount: browserMessages.filter(
            (message) => message.startsWith("error:") || message.startsWith("pageerror:")
          ).length,
          warningCount: browserMessages.filter((message) => message.startsWith("warning:"))
            .length,
        },
        limitations: [
          "自动浏览器几何、输入设备与交互检查不替代精确 Hash 绑定的人工视觉接受。",
          "Edge-through-node 使用渲染后 SVG 路径采样；静态 Geometry Validator 仍作为独立机器门禁。",
        ],
      };
      fs.writeFileSync(measurementOutput, JSON.stringify(measurement, null, 2) + "\n", "utf8");
    }
    process.stdout.write(JSON.stringify(payload, null, 2) + "\n");
    if (payload.status !== "passed") process.exitCode = 1;
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error.stack || String(error));
  process.exit(2);
});
