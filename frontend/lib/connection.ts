/** Whether the last attempt to reach the server failed. lib/api.ts sets it when a request cannot reach the server (no connection, timeout) and clears it as soon as the server answers at all
 *  (even with an error), so the app can say plainly that the server is unreachable, keep saying it until it is not, and offer a retry. Nothing here is stored. */
let down = false;
let why = "";
const subscribers = new Set<() => void>();

export const connection = {
  subscribe(cb: () => void) { subscribers.add(cb); return () => { subscribers.delete(cb); }; },
  isDown: () => down,
  reason: () => why,
  markDown(reason: string) { if (!down || why !== reason) { down = true; why = reason; subscribers.forEach((f) => f()); } },
  markUp() { if (down) { down = false; why = ""; subscribers.forEach((f) => f()); } },
};
