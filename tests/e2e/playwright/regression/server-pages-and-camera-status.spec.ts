import { test, expect } from "@playwright/test";

test("server live and recordings pages render core controls", async ({ page, baseURL }) => {
  await page.goto(`${baseURL}/login`);
  await page.locator("#login-username").fill("admin");
  await page.locator("#login-password").fill("pass1234");
  await page.locator("#btn-login").click();

  await page.goto(`${baseURL}/live`);
  await expect(page.getByRole("heading", { name: "Live View" })).toBeVisible();
  await expect(page.locator("#live-camera-select")).toBeVisible();
  await expect(page.locator("#live-video")).toBeVisible();

  await page.goto(`${baseURL}/recordings`);
  await expect(page.getByText(/recordings/i)).toBeVisible();
});

test("camera status login and device page render", async ({ page }) => {
  await page.goto("https://127.0.0.1:5444/login");
  await page.locator("#username").fill("admin");
  await page.locator("#password").fill("pass1234");
  await page.getByRole("button", { name: "Login" }).click();

  await expect(page.getByText("Device Info")).toBeVisible();
  await expect(page.getByText("Connection")).toBeVisible();
  await expect(page.getByText("Server Pairing")).toBeVisible();
  await expect(page.getByText("System Health")).toBeVisible();
});
