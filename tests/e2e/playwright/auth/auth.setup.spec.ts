import { test, expect } from "@playwright/test";

test("server login page is reachable", async ({ page }) => {
  await page.goto("https://127.0.0.1:5443/login");
  await expect(page.getByRole("button", { name: "Sign In" })).toBeVisible();
});

test("camera login page is reachable", async ({ page }) => {
  await page.goto("https://127.0.0.1:5444/login");
  await expect(page.getByRole("button", { name: "Login" })).toBeVisible();
});
