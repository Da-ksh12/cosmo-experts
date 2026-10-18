"use client";

import { useEffect, useState } from "react";
import { api, blob } from "@/lib/api";
import { Dialog, LoadingState, Note } from "@/components/ui";

type Detail = { document: { filename: string; mime?: string }; pages: { page_no: number; text: string }[] };

/** Shows the original stored file (authorised blob fetch). Text formats fall back to the extracted text. */
export default function DocumentPreview({ id, name, mime, onClose }: { id: string; name: string; mime: string; onClose: () => void }) {
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true; let made = "";
    (async () => {
      try {
        if (mime === "application/pdf" || mime.startsWith("image/")) {
          const b = await blob(`/api/documents/${id}/file`);
          if (!live) return;
          made = URL.createObjectURL(b); setUrl(made);
        } else {
          const d = await api<Detail>(`/api/documents/${id}`);
          if (live) setText(d.pages.map((p) => p.text).join("\n\n"));
        }
      } catch (e) { if (live) setError(e instanceof Error ? e.message : "We couldn't open this file."); }
    })();
    return () => { live = false; if (made) URL.revokeObjectURL(made); };
  }, [id, mime]);

  return (
    <Dialog title={name} onClose={onClose} wide>
      {error ? <Note tone="bad">{error}</Note>
        : url ? (mime === "application/pdf" ? <iframe src={url} title={`Preview of ${name}`} className="h-[70dvh] w-full rounded-2xl border border-line bg-white" />
          // eslint-disable-next-line @next/next/no-img-element
          : <img src={url} alt={`Preview of ${name}`} className="mx-auto max-h-[70dvh] rounded-2xl" />)
        : text ? <pre className="max-h-[70dvh] overflow-auto whitespace-pre-wrap rounded-2xl bg-white p-4 text-sm">{text}</pre>
        : <LoadingState label="Opening the file…" />}
    </Dialog>
  );
}
