import { expect, test } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Captures real screenshots of the running app (Demo Mode, synthetic frame, simulated device) for docs/demo/screens.
const out = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../docs/demo/screens");

test("capture demo screenshots", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8100/api/hardware/disconnect");
  await page.setViewportSize({ width: 1440, height: 860 });
  await page.goto("/");
  await page.screenshot({ path: path.join(out, "01-start.png") });

  await page.getByRole("button", { name: "New Demo project" }).click();
  await page.getByRole("button", { name: "Load synthetic demo frame" }).click();
  await page.getByRole("button", { name: "Scan components" }).click();
  await expect(page.getByText("Inventory · 6 available")).toBeVisible();
  await page.screenshot({ path: path.join(out, "02-scan-inventory.png") });

  for (const [name, val] of [["Resistor A", "220"], ["Resistor B", "10000"], ["Red LED", "2"]] as const) {
    const input = page.locator("li", { hasText: name }).getByPlaceholder("unknown");
    await input.fill(val);
    await input.press("Enter");
  }
  for (let i = 0; i < 10; i++) {
    const btn = page.getByRole("button", { name: "Confirm", exact: true }).first();
    if (!(await btn.isVisible().catch(() => false))) break;
    await btn.click();
    await page.waitForTimeout(250);
  }
  await page.getByRole("button", { name: "Design with Muse Spark" }).click();
  await expect(page.getByText("led.current_limit")).toBeVisible();
  await page.screenshot({ path: path.join(out, "03-design-validation.png") });

  await page.getByRole("tab", { name: "Hardware" }).click();
  await expect(page.getByText("read-only · no port opened")).toBeVisible();
  await page.screenshot({ path: path.join(out, "04-hardware-diagnostics.png") });
});
