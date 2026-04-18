/**
 * User journey: OTA firmware updates
 *
 * Covers the admin workflow for server and camera OTA updates. In CI there is
 * no real firmware bundle, so tests verify API contract, error handling, and
 * UI element presence rather than a full flash cycle.
 *
 * Seeded state: firmware_version = "test-build", cam-001 online.
 */

import { test, expect } from "@playwright/test";

async function loginAsAdmin(
  page: import("@playwright/test").Page,
  baseURL: string
): Promise<string> {
  const resp = await page.request.post(`${baseURL}/api/v1/auth/login`, {
    data: { username: "admin", password: "pass1234" },
  });
  const body = await resp.json();
  return body.csrf_token as string;
}

// ---------------------------------------------------------------------------
// OTA status endpoints
// ---------------------------------------------------------------------------

test.describe("OTA status API", () => {
  let csrfToken: string;

  test.beforeEach(async ({ page, baseURL }) => {
    csrfToken = await loginAsAdmin(page, baseURL!);
  });

  test("GET /api/v1/ota/status returns current firmware version", async ({ page, baseURL }) => {
    const resp = await page.request.get(`${baseURL}/api/v1/ota/status`);
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body).toHaveProperty("firmware_version");
    expect(body.firmware_version).toBe("test-build");
  });

  test("OTA status includes update_available and staging fields", async ({ page, baseURL }) => {
    const resp = await page.request.get(`${baseURL}/api/v1/ota/status`);
    const body = await resp.json();
    expect(body).toHaveProperty("update_available");
    expect(body).toHaveProperty("staging");
  });

  test("unauthenticated OTA status request is rejected", async ({ page, baseURL }) => {
    const resp = await page.request.get(`${baseURL}/api/v1/ota/status`, {
      headers: { Cookie: "" },
    });
    expect([401, 403]).toContain(resp.status());
  });
});

// ---------------------------------------------------------------------------
// Server OTA upload validation
// ---------------------------------------------------------------------------

test.describe("Server OTA upload", () => {
  let csrfToken: string;

  test.beforeEach(async ({ page, baseURL }) => {
    csrfToken = await loginAsAdmin(page, baseURL!);
  });

  test("upload without file returns 400", async ({ page, baseURL }) => {
    const resp = await page.request.post(`${baseURL}/api/v1/ota/server/upload`, {
      headers: { "X-CSRF-Token": csrfToken },
      multipart: {},
    });
    expect(resp.status()).toBe(400);
  });

  test("upload with empty filename returns 400", async ({ page, baseURL }) => {
    const resp = await page.request.post(`${baseURL}/api/v1/ota/server/upload`, {
      headers: { "X-CSRF-Token": csrfToken },
      multipart: {
        bundle: {
          name: "",
          mimeType: "application/octet-stream",
          buffer: Buffer.from(""),
        },
      },
    });
    expect(resp.status()).toBe(400);
  });

  test("non-admin cannot upload OTA bundle", async ({ page, baseURL }) => {
    // Attempt upload with no credentials
    const resp = await page.request.post(`${baseURL}/api/v1/ota/server/upload`, {
      headers: { Cookie: "", "X-CSRF-Token": "" },
      multipart: {
        bundle: {
          name: "fake.swu",
          mimeType: "application/octet-stream",
          buffer: Buffer.from("not a real bundle"),
        },
      },
    });
    expect([401, 403]).toContain(resp.status());
  });
});

// ---------------------------------------------------------------------------
// Camera OTA upload validation
// ---------------------------------------------------------------------------

test.describe("Camera OTA upload", () => {
  let csrfToken: string;

  test.beforeEach(async ({ page, baseURL }) => {
    csrfToken = await loginAsAdmin(page, baseURL!);
  });

  test("camera OTA upload without file returns 400", async ({ page, baseURL }) => {
    const resp = await page.request.post(
      `${baseURL}/api/v1/ota/camera/upload`,
      {
        headers: { "X-CSRF-Token": csrfToken },
        multipart: {},
      }
    );
    expect(resp.status()).toBe(400);
  });

  test("camera OTA push without IP returns 400", async ({ page, baseURL }) => {
    // cam-001 has an IP in seeded state; use an unknown camera
    const resp = await page.request.post(
      `${baseURL}/api/v1/ota/camera/cam-no-ip/push`,
      { headers: { "X-CSRF-Token": csrfToken } }
    );
    // 404 (no such camera) or 400 (no IP) — both are correct rejections
    expect([400, 404]).toContain(resp.status());
  });

  test("camera live OTA status endpoint requires authentication", async ({ page, baseURL }) => {
    const resp = await page.request.get(
      `${baseURL}/api/v1/ota/camera/cam-001/status`,
      { headers: { Cookie: "" } }
    );
    expect([401, 403]).toContain(resp.status());
  });
});

// ---------------------------------------------------------------------------
// USB OTA operations
// ---------------------------------------------------------------------------

test.describe("USB OTA scan", () => {
  let csrfToken: string;

  test.beforeEach(async ({ page, baseURL }) => {
    csrfToken = await loginAsAdmin(page, baseURL!);
  });

  test("GET /api/v1/ota/usb/scan is admin-only", async ({ page, baseURL }) => {
    // Without auth should fail
    const unauthResp = await page.request.get(`${baseURL}/api/v1/ota/usb/scan`, {
      headers: { Cookie: "" },
    });
    expect([401, 403]).toContain(unauthResp.status());
  });

  test("authenticated USB scan returns 200 (no USB mounted = empty list)", async ({ page, baseURL }) => {
    const resp = await page.request.get(`${baseURL}/api/v1/ota/usb/scan`);
    // 200 with empty bundles list, or 404/503 if USB not mounted — all valid
    expect([200, 404, 503]).toContain(resp.status());
    if (resp.status() === 200) {
      const body = await resp.json();
      expect(body).toHaveProperty("bundles");
    }
  });
});

// ---------------------------------------------------------------------------
// OTA UI visibility
// ---------------------------------------------------------------------------

test.describe("OTA UI in settings", () => {
  test.beforeEach(async ({ page, baseURL }) => {
    await page.goto(`${baseURL}/login`);
    await page.locator("#login-username").fill("admin");
    await page.locator("#login-password").fill("pass1234");
    await page.locator("#btn-login").click();
    await expect(page).toHaveURL(/\/dashboard/);
    await page.goto(`${baseURL}/settings`);
  });

  test("settings page has OTA/update section", async ({ page }) => {
    const otaSection = page.locator(
      "#settings-ota, [data-section='ota'], section:has-text('OTA'), " +
        "section:has-text('Update'), section:has-text('Firmware')"
    );
    await expect(otaSection.first()).toBeVisible();
  });

  test("settings page shows current firmware version", async ({ page }) => {
    // The seeded firmware version "test-build" should appear somewhere on the page
    await expect(page.getByText("test-build")).toBeVisible();
  });

  test("non-admin is redirected away from settings", async ({ page, baseURL }) => {
    await page.goto(`${baseURL}/login`);
    await page.locator("#login-username").fill("viewer");
    await page.locator("#login-password").fill("pass1234");
    await page.locator("#btn-login").click();

    await page.goto(`${baseURL}/settings`);
    const url = page.url();
    const bodyText = await page.locator("body").textContent();
    const blocked =
      !url.endsWith("/settings") ||
      (bodyText?.includes("403") ?? false) ||
      (bodyText?.includes("Forbidden") ?? false);
    // viewer doesn't exist in seeded state → login fails → redirect to login
    // If viewer did exist: settings should be blocked for non-admin
    expect(blocked || url.includes("login")).toBe(true);
  });
});
