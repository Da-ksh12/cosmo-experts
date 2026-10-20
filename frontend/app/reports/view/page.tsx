"use client";

import { Suspense } from "react";
import ReportPage from "../[id]/page.web";

/** The Android bundle is a static export and cannot contain a page per report. This one page shows any report, with the id in the address (?id=...). */
export default function ReportViewPage() {
  return <Suspense fallback={null}><ReportPage /></Suspense>;
}
