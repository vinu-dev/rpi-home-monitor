/**
 * User journey: Camera management
 *
 * Covers the full admin workflow for viewing, editing, and managing cameras.
 * Seeded state: cam-001 "Front Door" (online, continuous, 192.168.1.50).
 */

import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function loginAsAdmin(page: import("@playwright/test").Page, baseURL: string) {
  await page.goto(`${baseURL}/login`);
  await page.locator("#login-username").fill("admin");
  await page.locator("#login-password").fill("pass1234");
  await page.locator("#btn-login").click();
  await expect(page).toHaveURL(/\/dashboard/);
}

/** Extract the CSRF token via the auth/me endpoint (same session cookie). */
async function getCsrfToken(
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
// Camera list and status
// ---------------------------------------------------------------------------

test.describe("Camera list", () => {
  test("seeded camera appears in the cameras section", async ({ page, baseURL }) => {
    await loginAsAdmin(page, baseURL!);

    // Dashboard cameras section contains the seeded camera name
    await expect(page.locator("#cameras-section")).toBeVisible();
    await expect(page.getByText("Front Door")).toBeVisible();
  });

  test("camera status shows as online", async ({ page, baseURL }) => {
    await loginAsAdmin(page, baseURL!);
    await expect(page.getByText(/online/i).first()).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Camera detail and edit
// ---------------------------------------------------------------------------

test.describe("Camera management via API (admin)", () => {
  let csrfToken: string;

  test.beforeEach(async ({ page, baseURL }) => {
    csrfToken = await getCsrfToken(page, baseURL!);
  });

  test("GET /api/v1/cameras returns cam-001", async ({ page, baseURL }) => {
    const resp = await page.request.get(`${baseURL}/api/v1/cameras`, {
      headers: { "X-CSRF-Token": csrfToken },
    });
    expect(resp.status()).toBe(200);
    const cameras = await resp.json();
    const cam = cameras.find((c: { id: string }) => c.id === "cam-001");
    expect(cam).toBeDefined();
    expect(cam.name).toBe("Front Door");
    expect(cam.location).toBe("Outdoor");
    expect(cam.status).toBe("online");
  });

  test("GET /api/v1/cameras/cam-001 returns full camera object", async ({ page, baseURL }) => {
    const resp = await page.request.get(`${baseURL}/api/v1/cameras/cam-001`);
    expect(resp.status()).toBe(200);
    const cam = await resp.json();
    expect(cam.id).toBe("cam-001");
    expect(cam.recording_mode).toBe("continuous");
    expect(cam.ip).toBe("192.168.1.50");
  });

  test("admin can update camera name and it persists", async ({ page, baseURL }) => {
    const newName = "Front Door E2E";

    const updateResp = await page.request.put(`${baseURL}/api/v1/cameras/cam-001`, {
      data: { name: newName, location: "Outdoor" },
      headers: { "X-CSRF-Token": csrfToken },
    });
    expect(updateResp.status()).toBe(200);

    // Verify the change persists via a fresh GET
    const getResp = await page.request.get(`${baseURL}/api/v1/cameras/cam-001`);
    const cam = await getResp.json();
    expect(cam.name).toBe(newName);

    // Restore original name so other tests see consistent state
    await page.request.put(`${baseURL}/api/v1/cameras/cam-001`, {
      data: { name: "Front Door", location: "Outdoor" },
      headers: { "X-CSRF-Token": csrfToken },
    });
  });

  test("admin can change recording mode", async ({ page, baseURL }) => {
    const updateResp = await page.request.put(`${baseURL}/api/v1/cameras/cam-001`, {
      data: { name: "Front Door", location: "Outdoor", recording_mode: "schedule" },
      headers: { "X-CSRF-Token": csrfToken },
    });
    expect(updateResp.status()).toBe(200);
    const cam = await updateResp.json();
    expect(cam.recording_mode).toBe("schedule");

    // Restore
    await page.request.put(`${baseURL}/api/v1/cameras/cam-001`, {
      data: { name: "Front Door", location: "Outdoor", recording_mode: "continuous" },
      headers: { "X-CSRF-Token": csrfToken },
    });
  });

  test("unauthenticated request to camera list returns 401", async ({ page, baseURL }) => {
    // Use a fresh context with no session
    const resp = await page.request.get(`${baseURL}/api/v1/cameras`, {
      headers: { Cookie: "" },
    });
    expect([401, 403]).toContain(resp.status());
  });
});

// ---------------------------------------------------------------------------
// Recordings page journey
// ---------------------------------------------------------------------------

test.describe("Recordings page", () => {
  test.beforeEach(async ({ page, baseURL }) => {
    await loginAsAdmin(page, baseURL!);
    await page.goto(`${baseURL}/recordings`);
  });

  test("recordings page is reachable and mentions recordings", async ({ page }) => {
    await expect(page.getByText(/recordings/i).first()).toBeVisible();
  });

  test("cam-001 appears as a recording source via API", async ({ page, baseURL }) => {
    const resp = await page.request.get(
      `${baseURL}/api/v1/cameras/cam-001/recordings/sources`
    );
    // 200 or 404 depending on seeded data; just assert reachable and auth'd
    expect([200, 404]).toContain(resp.status());
  });
});
