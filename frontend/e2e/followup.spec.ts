import { expect, test, type WebSocketRoute } from "@playwright/test";
import { mockBackend } from "./fixtures";

/** Like fakeVoiceServer, but in wake mode: after the answer has "played",
 * the server opens a short follow-up window and closes it again. */
function followupServer(ws: WebSocketRoute) {
  const send = (msg: object) => ws.send(JSON.stringify(msg));
  ws.onMessage((raw) => {
    if (typeof raw !== "string") return;
    const msg = JSON.parse(raw) as { type: string; text?: string };
    if (msg.type === "auth") {
      send({
        type: "ready",
        session_id: "e2e",
        conversation_id: "e2e0e2e0e2e0e2e0",
        mode: "wake",
        wake_word_enabled: true,
        wake_phrase: "Hey Cypher",
      });
      send({ type: "state", state: "wake_listening", turn_id: 0 });
    } else if (msg.type === "text") {
      send({ type: "transcript", text: msg.text, turn_id: 1 });
      send({ type: "state", state: "thinking", turn_id: 1 });
      send({ type: "reply", text: "Es ist zwölf Uhr.", turn_id: 1 });
      send({ type: "state", state: "speaking", turn_id: 1 });
      send({ type: "audio_end", turn_id: 1 });
      // answer played -> window open (the server owns the timer)
      send({ type: "state", state: "listening", turn_id: 1, followup_seconds: 2, followup_total: 2 });
      setTimeout(() => send({ type: "state", state: "wake_listening", turn_id: 1 }), 2000);
    }
  });
}

test("after an answer Cypher keeps listening for a while, then waits for the wake word again", async ({ page }) => {
  await mockBackend(page);
  await page.routeWebSocket(/\/api\/voice\/ws$/, followupServer);
  await page.goto("/");
  const input = page.getByRole("textbox", { name: "Befehl tippen" });
  await input.fill("Wie spät ist es?");
  await input.press("Enter");

  await expect(page.getByText("Ich höre weiter zu - frag einfach weiter.")).toBeVisible();
  await expect(page.getByText("Es ist zwölf Uhr.")).toBeVisible(); // the answer stays readable
  await expect(page.getByRole("button", { name: "Weiter zuhören beenden" })).toHaveClass(/core--followup/);

  // the server closes the window -> back to the wake word
  await expect(page.getByText("Ich höre weiter zu - frag einfach weiter.")).toBeHidden({ timeout: 5000 });
  await expect(page.locator(".core")).not.toHaveClass(/core--followup/);
});
