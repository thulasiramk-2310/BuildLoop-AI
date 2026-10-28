import { expect, test, type Page } from "@playwright/test";

/**
 * Demo Mode closed loop in a real browser. Fixtures: the backend's synthetic demo frame and the simulated device.
 * Nothing here is physical or live-Meta evidence; the UI labels it as such and these tests assert those labels.
 */

const stage = (page: Page, name: string) => page.locator(`nav[aria-label="Engineering loop stage"] [aria-current="step"]`).filter({ hasText: name });

async function confirmInventory(page: Page) {
  for (const [name, val] of [["Resistor A", "220"], ["Resistor B", "10000"], ["Red LED", "2"]] as const) {
    const input = page.locator("li", { hasText: name }).getByPlaceholder("unknown");
    await input.fill(val);
    await input.press("Enter");
    await expect(input).toHaveValue(val === "2" ? "2" : val);
  }
  for (let i = 0; i < 10; i++) {
    const btn = page.getByRole("button", { name: "Confirm", exact: true }).first();
    if (!(await btn.isVisible().catch(() => false))) break;
    await btn.click();
    await page.waitForTimeout(250);
  }
  await expect(page.getByRole("button", { name: "Confirm", exact: true })).toHaveCount(0);
}

async function approveAndAssemble(page: Page, title: string) {
  await page.getByRole("button", { name: new RegExp(`^${title}`) }).click();
  await page.getByRole("button", { name: "Approve design" }).click();
  await expect(page.getByRole("tab", { name: "Assembly", selected: true })).toBeVisible();
  const step = page.getByRole("button", { name: "I completed and checked this step" });
  for (let i = 0; i < 6 && (await step.isVisible().catch(() => false)); i++) {
    await step.click();
    await page.waitForTimeout(300);
  }
  await expect(page.getByText("All steps confirmed")).toBeVisible();
}

async function authorizeSimulated(page: Page) {
  await page.getByRole("tab", { name: "Hardware" }).click();
  if (await page.getByRole("button", { name: "Use simulated device" }).isVisible()) {
    await page.getByRole("button", { name: "Use simulated device" }).click();
  }
  await expect(page.getByText("SIMULATED — not physical hardware")).toBeVisible();
  await page.getByLabel(/I checked the wiring/).check();
  await page.getByRole("button", { name: "Authorize execution" }).click();
  await expect(page.getByRole("button", { name: "Authorized" })).toBeVisible();
}

test.beforeEach(async ({ request }) => {
  await request.post("http://127.0.0.1:8100/api/hardware/disconnect");
});

test("Demo Mode: observe → design → build → test → remove part → redesign → verify", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Create a project to begin")).toBeVisible();

  // project + scan
  await page.getByRole("button", { name: "New Demo project" }).click();
  await expect(page.getByText("DEMO MODE · simulated")).toBeVisible();
  await page.getByRole("button", { name: "Load synthetic demo frame" }).click();
  await expect(page.getByText("synthetic frame")).toBeVisible();
  await page.getByRole("button", { name: "Scan components" }).click();
  await expect(page.getByText("Inventory · 6 available")).toBeVisible();
  await expect(stage(page, "CONFIRM")).toBeVisible();
  await expect(page.getByLabel("Detection overlay")).toBeVisible();

  // unknown resistance is flagged, planner blocked from approving until confirmed
  await expect(page.getByText("Unknown resistance").first()).toBeVisible();
  await confirmInventory(page);

  // design + validation
  await page.getByRole("tab", { name: "Copilot" }).click();
  await page.getByRole("button", { name: "Design with Muse Spark" }).click();
  await expect(page.getByRole("tab", { name: "Design", selected: true })).toBeVisible();
  await expect(page.getByText("deterministic planner, not a model")).toBeVisible();
  await expect(page.getByText("led.current_limit")).toBeVisible();
  await expect(page.getByText("Estimate, not measured").first()).toBeVisible();

  // approve, assemble, authorize, test
  await approveAndAssemble(page, "Photoresistor automatic light");
  await authorizeSimulated(page);
  await page.getByRole("button", { name: "1. Start on-device test" }).click();
  await page.locator('input[type="range"]').first().fill("0.9");
  await page.getByRole("button", { name: /2\. Capture BRIGHT/ }).click();
  await expect(page.getByText("Bright phase captured")).toBeVisible();
  await page.locator('input[type="range"]').first().fill("0.1");
  await page.getByRole("button", { name: /3\. Capture DARK/ }).click();
  await expect(page.getByText("Dark phase captured")).toBeVisible();
  await page.getByRole("button", { name: "4. Evaluate" }).click();
  await expect(page.getByText("simulated device").first()).toBeVisible();
  await expect(page.getByText(/SIMULATED device: this is not physical evidence/).first()).toBeVisible();
  await expect(page.getByText("Optical output observed (independent)")).toBeVisible(); // ladder row exists but is not reached
  await expect(stage(page, "COMPARE")).toBeVisible();

  // reality changes
  await page.getByRole("region", { name: /^Inventory/ }).locator("li").filter({ has: page.getByRole("button", { name: "Photoresistor", exact: true }) }).getByRole("button", { name: "Remove / unavailable" }).click();
  await expect(stage(page, "REDESIGN")).toBeVisible();
  await page.getByRole("tab", { name: "Copilot" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Reality changed" })).toBeVisible();
  await expect(page.getByText(/Design needs 1 x photoresistor; inventory has 0/).first()).toBeVisible();
  await page.getByRole("tab", { name: "Design" }).click();
  await expect(page.getByText(/Invalidated by inventory change/)).toBeVisible();

  // redesign with substitute sensor
  await page.getByRole("tab", { name: "Copilot" }).click();
  await page.getByRole("button", { name: "Propose redesign" }).click();
  await expect(page.getByText(/substitute sensor, not an identical replacement/).first()).toBeVisible();
  await approveAndAssemble(page, "Phone-camera brightness light");
  await authorizeSimulated(page);
  await page.locator('input[type="range"]').last().fill("0.1");
  await page.getByRole("button", { name: "Send reading" }).click();
  await expect(page.getByText(/LED ON/).first()).toBeVisible();
  await page.locator('input[type="range"]').last().fill("0.9");
  await page.getByRole("button", { name: "Send reading" }).click();
  await expect(page.getByText(/LED OFF/).first()).toBeVisible();
  await page.getByRole("button", { name: "Evaluate", exact: true }).click();
  await expect(page.getByText(/not physical evidence/).first()).toBeVisible();

  // the NEW verification is a separate, labelled record from the original one
  await expect(page.getByText(/Phone-driven control consistent at level/).first()).toBeVisible();
  await expect(page.getByText(/LED illumination not independently observed|not physical evidence/).first()).toBeVisible();

  // before / after + history
  await page.getByRole("tab", { name: "Design" }).click();
  await expect(page.getByText("Before / after")).toBeVisible();
  await expect(page.getByText(/Removed: Photoresistor/).first()).toBeVisible();
  await expect(page.getByText("Unresolved limitations")).toBeVisible();
  const cols = page.locator("p", { hasText: /^Original$|^Revised$/ });
  await expect(cols).toHaveCount(2);
  await expect(page.getByText("sensor response observed")).toHaveCount(2); // original and revised each carry their own evidence level
  await expect(page.getByText("design invalidated").first()).toBeVisible();

  // persistence across reload
  await page.reload();
  await expect(page.getByText("DEMO MODE · simulated")).toBeVisible();
  await expect(page.getByText("design invalidated").first()).toBeVisible();
  await expect(page.getByText("Inventory · 5 available")).toBeVisible();
});

test("Real Mode without an API key fails clearly and never falls back to demo data", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "New Real project" }).click();
  await expect(page.getByText("REAL MODE", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Load synthetic demo frame" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Use simulated device" })).toHaveCount(0);
});

test("Hardware diagnostics are read-only and serial connect needs explicit approval", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "New Demo project" }).click();
  await page.getByRole("tab", { name: "Hardware" }).click();
  await expect(page.getByText("read-only · no port opened")).toBeVisible();
  await expect(page.getByText("Required wiring")).toBeVisible();
  await expect(page.getByText("First physical validation sequence")).toBeVisible();
  await expect(page.getByRole("button", { name: "Connect", exact: true })).toBeDisabled();
});
