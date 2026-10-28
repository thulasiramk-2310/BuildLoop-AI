import { expect, test } from "@playwright/test";

/**
 * Phone page with Chrome's SYNTHETIC camera feed (flag below). The frames are real getUserMedia frames, but the source is a
 * browser test pattern, not a physical phone camera. A real phone over https must still be tested by hand.
 */
test.use({
  permissions: ["camera"],
  launchOptions: { args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--disable-gpu"] },
});

const API = "http://127.0.0.1:8100";

test("phone page captures real frames, uploads a snapshot, reports connection, guards calibration", async ({ page, request, context }) => {
  const created = await request.post(`${API}/api/projects`, { data: { name: "phone e2e", mode: "demo" } });
  const pid = (await created.json()).id as string;

  await page.goto(`/phone?project=${pid}`);
  await expect(page.getByText(/Laptop link:/)).toBeVisible();
  await expect(page.getByText("online", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Start camera" }).click();
  await expect(page.getByText(/Camera live/)).toBeVisible();

  // brightness is an estimate and must refuse to stream until two distinct references exist
  await expect(page.getByText(/uncalibrated/)).toBeVisible();
  await expect(page.getByText(/NOT lux/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Stream brightness to LED design" })).toBeDisabled();

  // snapshot goes to the laptop backend only (not to Meta)
  await page.getByRole("button", { name: "Send snapshot to laptop" }).click();
  await expect(page.getByText(/not sent to Meta/)).toBeVisible();
  const snap = await request.get(`${API}/api/phone/${pid}/snapshot.jpg`);
  expect(snap.status()).toBe(200);
  const bytes = (await snap.body()).length;
  expect(bytes).toBeGreaterThan(2000); // an actual JPEG frame, not an empty placeholder
  const status = await (await request.get(`${API}/api/phone/${pid}/status`)).json();
  expect(status.connected).toBe(true);
  expect(status.has_snapshot).toBe(true);

  // laptop side shows the phone as connected with the preview
  const laptop = await context.newPage();
  await laptop.addInitScript((id) => localStorage.setItem("buildloop.project", id), pid);
  await laptop.goto("/");
  await laptop.getByRole("tab", { name: "Hardware" }).click();
  await expect(laptop.getByText("phone connected")).toBeVisible();
  await expect(laptop.getByAltText("Latest phone snapshot")).toBeVisible();

  // phone-triggered scan: detections are model predictions, not confirmed identities
  await page.getByRole("button", { name: /Scan this view/ }).click();
  await expect(page.getByText(/Scan sent to laptop \(demo fixture\)/)).toBeVisible();
  const comps = await (await request.get(`${API}/api/inventory/${pid}`)).json();
  expect(comps.length).toBe(6);
  expect(comps.every((c: { status: string }) => c.status === "detected" || c.status === "uncertain")).toBe(true);
  await laptop.reload();
  await expect(laptop.getByText("Inventory · 6 available")).toBeVisible();
  await expect(laptop.getByText("USER CONFIRMED")).toHaveCount(0);

  // editable inventory: rename one detection, discard another as a false positive
  const inv = laptop.getByRole("region", { name: /^Inventory/ });
  await inv.getByRole("button", { name: "Breadboard", exact: true }).click();
  const name = inv.getByLabel("Name");
  await name.fill("Main breadboard");
  await name.press("Enter");
  await expect(inv.getByRole("button", { name: "Main breadboard", exact: true })).toBeVisible();
  await inv.getByRole("button", { name: "Discard false detection" }).click();
  await expect(laptop.getByText("Inventory · 5 available")).toBeVisible();
  await expect(inv.getByRole("button", { name: "Main breadboard", exact: true })).toHaveCount(0);
});
