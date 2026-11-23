import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";

// Most tests here are synchronous and heavy (a whole setup page drawn in jsdom), and Vitest runs the tests of a
// file back to back on promises alone. A worker can then go a minute without its event loop turning, and when it
// turns at last the timer of a call to the test runner fires before the runner's answer is read: the run ends
// with `[vitest-worker]: Timeout calling "onTaskUpdate"` and a failing exit code although every test passed.
// Letting the loop turn once after each test keeps the worker in touch. A message channel is used because it is
// a real task whatever a test did to the timers.
afterEach(() => new Promise<void>((resolve) => {
  const channel = new MessageChannel();
  channel.port1.onmessage = () => { channel.port1.close(); resolve(); };
  channel.port2.postMessage(null);
}));
