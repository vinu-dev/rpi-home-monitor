import { test, expect } from "@playwright/test";

async function login(page, baseURL: string | undefined) {
  await page.goto(`${baseURL}/login`);
  await page.locator("#login-username").fill("admin");
  await page.locator("#login-password").fill("pass1234");
  await Promise.all([
    page.waitForURL(/\/dashboard/),
    page.locator("#btn-login").click(),
  ]);
}

async function expectNoHorizontalOverflow(page) {
  const overflow = await page.evaluate(() => {
    const width = Math.max(
      document.documentElement.scrollWidth,
      document.body.scrollWidth,
    );
    return width - window.innerWidth;
  });
  expect(overflow).toBeLessThanOrEqual(4);
}

async function expectDialogScrollsWithinViewport(page, selector: string) {
  const dialog = page.locator(selector);
  await expect(dialog).toBeVisible();
  const metrics = await dialog.evaluate((el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return {
      clientHeight: el.clientHeight,
      scrollHeight: el.scrollHeight,
      overflowY: style.overflowY,
      top: rect.top,
      bottom: rect.bottom,
      viewportHeight: window.innerHeight,
    };
  });
  expect(["auto", "scroll"]).toContain(metrics.overflowY);
  expect(metrics.bottom).toBeLessThanOrEqual(metrics.viewportHeight + 1);
  expect(metrics.top).toBeGreaterThanOrEqual(-1);
  return dialog;
}

test("server setup and dashboard flows are reachable", async ({ page, baseURL }) => {
  await login(page, baseURL);

  await expect(page).toHaveURL(/\/dashboard/);
  await expect(page.getByRole("heading", { name: "Home state" })).toBeVisible();
  // ADR-0018: the health grid was replaced by a status strip + four
  // summary tiles. "Recorder host" is the Tier-2 tile that subsumes the
  // old "System Health" section.
  await expect(page.getByText("Recorder host")).toBeVisible();
  // The word "Cameras" now appears in the status strip, the Tier-2 tile
  // and the roll-call section header — pin this assertion to the unique
  // section header id so it doesn't trip strict-mode multi-match.
  await expect(page.locator("#cameras-section")).toBeVisible();
});

test("redesigned server GUI covers pages, settings tabs, and widths", async ({
  page,
  baseURL,
}) => {
  test.setTimeout(90_000);
  await login(page, baseURL);

  const viewports = [
    { name: "phone", width: 430, height: 932 },
    { name: "tablet", width: 820, height: 1180 },
    { name: "laptop", width: 1440, height: 900 },
  ];
  const pages = [
    { path: "/dashboard", heading: "Home state" },
    { path: "/live", heading: "Live View" },
    { path: "/recordings", heading: "Recordings" },
    { path: "/events", heading: "Events" },
    { path: "/alerts", heading: "Alerts" },
    { path: "/settings", heading: "Settings" },
    { path: "/logs", heading: "Activity log" },
  ];

  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    for (const pageSpec of pages) {
      await page.goto(`${baseURL}${pageSpec.path}`);
      await expect(
        page.getByRole("heading", { name: pageSpec.heading }),
        `${viewport.name} ${pageSpec.path}`,
      ).toBeVisible();
      await expectNoHorizontalOverflow(page);
    }
  }

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${baseURL}/settings`);
  for (const tabName of [
    "System",
    "Network",
    "Tailscale",
    "Users",
    "Security",
    "Recording",
    "Storage",
    "Webhooks",
    "Updates",
    "Account",
    "Notifications",
  ]) {
    const tab = page.getByRole("button", { name: tabName });
    await expect(tab, `settings tab ${tabName}`).toBeVisible();
    await tab.click();
    await expectNoHorizontalOverflow(page);
  }

  await page.setViewportSize({ width: 430, height: 932 });
  await page.goto(`${baseURL}/settings`);
  const sectionPicker = page.getByLabel("Settings section");
  await expect(sectionPicker).toBeVisible();
  for (const tabName of [
    "System",
    "Network",
    "Tailscale",
    "Users",
    "Security",
    "Recording",
    "Storage",
    "Webhooks",
    "Updates",
    "Account",
    "Notifications",
  ]) {
    await sectionPicker.selectOption({ label: tabName });
    await expectNoHorizontalOverflow(page);
  }

  await page.setViewportSize({ width: 430, height: 932 });
  await page.goto(`${baseURL}/dashboard`);
  const cameraCard = page.locator(".camera-card", { hasText: "Front Door" });
  await expect(cameraCard).toBeVisible();
  await cameraCard.getByRole("button", { name: "Settings" }).click();

  const settingsDialog = await expectDialogScrollsWithinViewport(
    page,
    ".modal-card",
  );
  const settingsMetrics = await settingsDialog.evaluate((el) => ({
    clientHeight: el.clientHeight,
    scrollHeight: el.scrollHeight,
  }));
  expect(settingsMetrics.scrollHeight).toBeGreaterThan(
    settingsMetrics.clientHeight + 20,
  );
  await settingsDialog.evaluate((el) => {
    el.scrollTop = el.scrollHeight;
  });
  const saveButton = page.getByRole("button", { name: "Save & Apply" });
  const saveBounds = await saveButton.evaluate((el) => {
    const rect = el.getBoundingClientRect();
    return {
      bottom: rect.bottom,
      top: rect.top,
      viewportHeight: window.innerHeight,
    };
  });
  expect(saveBounds.top).toBeGreaterThanOrEqual(-1);
  expect(saveBounds.bottom).toBeLessThanOrEqual(saveBounds.viewportHeight + 1);

  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.locator(".modal-card")).toBeHidden();

  await page.goto(`${baseURL}/live`);
  const shareButton = page.getByRole("button", { name: "Share live view" });
  await expect(shareButton).toBeVisible();
  await shareButton.click();
  await expectDialogScrollsWithinViewport(page, ".share-modal__dialog");
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.locator("#share-modal")).toBeHidden();
});
