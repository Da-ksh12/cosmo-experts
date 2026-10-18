export { api, post } from "@/lib/api";

export function fmt(ts: number | null | undefined) {
  return ts ? new Date(ts * 1000).toLocaleString() : "";
}
