import { test, expect } from "@playwright/test";
import { description, mockStudio, studioFixture } from "./studio.fixture";

test("voice descriptions must be applied, survive reload, and reach generation unchanged", async ({
  page,
}) => {
  const { requests } = await mockStudio(page);
  await page.goto("/");
  await page
    .getByRole("textbox", { name: "Words to speak" })
    .fill("Hello from Reed.");
  await page
    .getByRole("textbox", { name: "Voice description" })
    .fill(description);
  await expect(
    page.getByRole("button", { name: "Generate speech" }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Apply voice", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Voice applied", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Generate speech" }),
  ).toBeEnabled();
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Voice applied", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("textbox", { name: "Voice description" })
    .fill(description + " A little slower.");
  await expect(
    page.getByRole("button", { name: "Generate speech" }),
  ).toBeDisabled();
  await page.keyboard.press("Control+Enter");
  expect(requests).toHaveLength(0);
  await page.getByRole("button", { name: "Apply changes" }).click();
  await page.getByRole("button", { name: "Generate speech" }).click();
  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0]).toMatchObject({
    instruct: description + " A little slower.",
    mode: "design",
    script: "Hello from Reed.",
    model_id: "test-design",
  });
  await page.keyboard.press("Control+Enter");
  expect(requests).toHaveLength(1);
});

test("saved voices recall the applied description and presets invalidate it until applied", async ({
  page,
}) => {
  await mockStudio(page);
  await page.goto("/");
  await page
    .getByRole("button", { name: "Warm narrator", exact: true })
    .click();
  await page.getByRole("button", { name: "Apply voice", exact: true }).click();
  await page.getByRole("button", { name: "Save voice", exact: true }).click();
  await page.getByRole("textbox", { name: "Voice name" }).fill("My narrator");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page
    .getByRole("button", { name: "Bright & playful", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Apply changes" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "My narrator", exact: true }).click();
  await expect(
    page.getByRole("textbox", { name: "Voice description" }),
  ).toHaveValue(description);
  await expect(
    page.getByRole("button", { name: "Voice applied", exact: true }),
  ).toBeVisible();
});

test("model drawer traps focus, closes with Escape, and restores focus", async ({
  page,
}) => {
  await mockStudio(page);
  await page.goto("/");
  const trigger = page.getByRole("button", { name: "Models", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Models & performance" });
  await expect(dialog).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Close models" }),
  ).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(
    page.getByRole("button", { name: "Measure", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
});

test("clone and preset modes retain their controls; layout fits narrow screens and dark theme persists", async ({
  page,
}) => {
  await mockStudio(page);
  await page.goto("/");
  await page
    .getByRole("group", { name: "Creation mode" })
    .getByRole("button", { name: "Clone", exact: true })
    .click();
  await expect(page.getByRole("button", { name: "Upload clip" })).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Transcript of the reference" }),
  ).toBeVisible();
  await page
    .getByRole("group", { name: "Creation mode" })
    .getByRole("button", { name: "Preset", exact: true })
    .click();
  await page.getByRole("button", { name: /Serena/ }).click();
  await page
    .getByRole("textbox", { name: "Words to speak" })
    .fill("A test of the preset voice.");
  await expect(
    page.getByRole("button", { name: "Generate · Serena" }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Use dark theme" }).click();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  for (const width of [1440, 1024, 768, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
  }
  await page.getByRole("button", { name: "Toggle history" }).click();
  await expect(
    page.getByRole("searchbox", { name: "Search takes" }),
  ).toBeVisible();
});

test("download progress and disconnected errors reflect backend state", async ({
  page,
}) => {
  const data = studioFixture();
  data.models[0].installed = false;
  data.models[0].download = {
    state: "downloading",
    expected: 3100000000,
    received: 1550000000,
    percent: 50,
    speed: 10000000,
    eta: 155,
    error: "",
  };
  await mockStudio(page, data);
  await page.goto("/");
  await page.getByRole("button", { name: "Models", exact: true }).click();
  await expect(
    page.getByRole("progressbar", { name: "Download progress" }),
  ).toHaveAttribute("aria-valuenow", "50");
  await expect(
    page.getByRole("button", { name: "Pause", exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await page.evaluate(() => (window as any).__events.onerror());
  await expect(page.getByRole("alert")).toContainText("disconnected");
});

test("history reuse restores the applied voice and old audio is clearly identified", async ({
  page,
}) => {
  const data = studioFixture();
  data.history = [
    {
      id: "take-1",
      created_at: new Date().toISOString(),
      mode: "design",
      script: "There is a story in every voice. This one starts with you.",
      language: "english",
      language_note: "",
      voice_description: description,
      speaker: "",
      instruct: "",
      model_id: "test-design",
      reference_id: "",
      duration_sec: 4.8,
      peaks: Array.from(
        { length: 110 },
        (_, i) => 0.12 + Math.abs(Math.sin(i * 1.7) * Math.cos(i * 0.21)) * 0.8,
      ),
      markers: [],
      wav_url: "/api/history/take-1/audio",
      mp3_url: "/api/history/take-1/mp3",
    },
  ];
  await mockStudio(page, data);
  await page.goto("/");
  await expect(
    page.getByText("Previously generated audio", { exact: false }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: /There is a story in every voice/ })
    .click();
  await page.getByRole("button", { name: "Reuse", exact: true }).click();
  await expect(
    page.getByRole("textbox", { name: "Words to speak" }),
  ).toHaveValue(data.history[0].script);
  await expect(
    page.getByRole("button", { name: "Voice applied", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Previously generated audio", { exact: false }),
  ).toBeHidden();
  await page.screenshot({
    path: test.info().outputPath("studio-light.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Use dark theme" }).click();
  await page.screenshot({
    path: test.info().outputPath("studio-dark.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: test.info().outputPath("studio-mobile.png"),
    fullPage: true,
  });
});
