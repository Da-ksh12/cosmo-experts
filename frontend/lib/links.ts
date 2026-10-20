import { IS_APP } from "@/lib/config";

/** Where a report opens. The static Android bundle cannot hold a page for every report id, so it uses one page (/reports/view/) that reads the id from the address; the web keeps /reports/<id>. */
export const reportHref = (id: string | undefined) => (IS_APP ? `/reports/view/?id=${encodeURIComponent(id ?? "")}` : `/reports/${id ?? ""}`);

/** Links created by the server (notifications) point at /reports/<id>; in the app they are mapped to the single report page. Anything else is returned unchanged. */
export function appHref(link: string): string {
  const m = IS_APP ? /^\/reports\/([^/?#]+)\/?$/.exec(link) : null;
  return m && m[1] !== "view" ? reportHref(decodeURIComponent(m[1])) : link;
}
