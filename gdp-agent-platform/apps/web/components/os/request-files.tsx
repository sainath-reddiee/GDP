"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState, useTransition } from "react";
import { Download, FileJson, FileText, Loader2, Paperclip, Trash2, Upload, X } from "lucide-react";
import { removeRequestFile, uploadRequestFiles, type UploadedRequestFile } from "@/app/os/requests/actions";
import { PACK_TEMPLATE } from "@/app/dashboard/onboarding/templates";
import type { RequestFile } from "@/lib/os/requests";
import { cn } from "@/lib/utils";

// Files on an onboarding request (apps/api/app/requests_api.py, V079): what describes the domain, in any format. They
// stay on the request until its domain exists, then become the domain's documents, read for the steward's review.

export const MAX_FILES = 10;
export const MAX_MB = 50;
export const size = (n: number | null | undefined) =>
  n == null ? "" : n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(0)} KB` : `${(n / (1024 * 1024)).toFixed(1)} MB`;

/** The sample domain JSON (services/knowledge/packs format, checked by tests/unit/test_onboarding_templates.py), to
 *  share with whoever describes the domain. */
export function SampleDomainJson({ className }: { className?: string }) {
  const download = () => {
    const url = URL.createObjectURL(new Blob([PACK_TEMPLATE], { type: "application/json" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: "sample_domain_pack.json" });
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <button type="button" onClick={download} className={cn("os-btn", className)} data-testid="sample-domain-json"
            title="A domain pack: the domain, its tables and columns with definitions, and glossary and rules">
      <FileJson className="h-3.5 w-3.5" /> Download sample domain JSON
    </button>
  );
}

/** Pick files before the request exists (the domain request form). Any format; the form uploads them once the request
 *  is filed. */
export function FilePicker({ files, onChange, disabled, label = "Domain context files" }: {
  files: File[]; onChange: (f: File[]) => void; disabled?: boolean; label?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  const big = files.filter((f) => f.size > MAX_MB * 1024 * 1024);
  const add = (list: FileList | null) => {
    if (!list) return;
    const seen = new Set(files.map((f) => `${f.name}:${f.size}`));
    onChange([...files, ...Array.from(list).filter((f) => !seen.has(`${f.name}:${f.size}`))].slice(0, MAX_FILES));
  };
  return (
    <div data-testid="request-file-picker">
      <span className="os-label">{label}</span>
      <div className="rounded-xl border border-dashed border-[var(--os-line)] p-3"
           onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); if (!disabled) add(e.dataTransfer.files); }}>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className="os-btn" disabled={disabled || files.length >= MAX_FILES} onClick={() => input.current?.click()}>
            <Paperclip className="h-3.5 w-3.5" /> Attach files
          </button>
          <SampleDomainJson />
          <span className="os-faint text-[11px]">Any format: data dictionary, model export, glossary, sample JSON, PDF. Up to {MAX_FILES} files of {MAX_MB} MB.</span>
          <input ref={input} type="file" multiple hidden onChange={(e) => { add(e.target.files); e.target.value = ""; }} aria-label="Domain context files" />
        </div>
        {files.length > 0 && (
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {files.map((f) => (
              <li key={`${f.name}:${f.size}`} className={cn("os-tag", f.size > MAX_MB * 1024 * 1024 ? "os-tag-red" : "os-tag-blue")}>
                <FileText className="h-3 w-3" />{f.name} <span className="opacity-70">{size(f.size)}</span>
                <button type="button" aria-label={`Remove ${f.name}`} onClick={() => onChange(files.filter((x) => x !== f))}><X className="h-3 w-3" /></button>
              </li>
            ))}
          </ul>
        )}
        {big.length > 0 && <p className="mt-1 text-[11px] text-[var(--os-red-ink)]">{big.map((f) => f.name).join(", ")}: larger than {MAX_MB} MB, will not be kept.</p>}
        <p className="os-faint mt-1.5 text-[11px]">They stay on the request until the domain exists, then become the domain&apos;s documents: read, and what they state proposed as knowledge for the steward to review.</p>
      </div>
    </div>
  );
}

export const formOf = (files: File[]) => {
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  return form;
};

export const uploadNote = (out: UploadedRequestFile[]) => {
  const kept = out.filter((o) => o.file_id).length;
  const other = out.filter((o) => !o.file_id).map((o) => `${o.file_name}: ${o.error ?? o.note ?? "not kept"}`);
  return `${kept} file${kept === 1 ? "" : "s"} attached.` + (other.length ? ` ${other.join("; ")}` : "");
};

/** The request's files: download, add (requester, domain owners, DOMAIN.ONBOARD) and where each went. */
export function RequestFiles({ requestId, files, mayAdd, previewing, domainId }: {
  requestId: string; files: RequestFile[]; mayAdd: boolean; previewing: boolean; domainId: string | null;
}) {
  const router = useRouter();
  const [pending, start] = useTransition();
  const [picked, setPicked] = useState<File[]>([]);
  const [line, setLine] = useState<{ fail: boolean; text: string } | null>(null);
  const upload = () => start(async () => {
    setLine(null);
    const r = await uploadRequestFiles(requestId, formOf(picked));
    if (!r.ok) { setLine({ fail: true, text: r.error }); return; }
    setPicked([]);
    setLine({ fail: false, text: uploadNote(r.files) });
    router.refresh();
  });
  const remove = (fileId: string) => start(async () => {
    const r = await removeRequestFile(requestId, fileId);
    setLine(r.ok ? { fail: false, text: "File removed from the request." } : { fail: true, text: r.error });
    if (r.ok) router.refresh();
  });
  return (
    <div data-testid="request-files">
      {files.length ? (
        <ul className="os-divide">
          {files.map((f) => (
            <li key={f.file_id} className="flex items-center justify-between gap-2 py-2">
              <div className="min-w-0">
                <a href={`/bff/request-file/${requestId}/${f.file_id}`} className="flex items-center gap-1.5 truncate text-[12.5px] font-bold underline-offset-2 hover:underline">
                  <Download className="h-3.5 w-3.5 shrink-0" /><span className="truncate">{f.file_name}</span>
                </a>
                <div className="os-faint text-[11px]">
                  {size(f.bytes)}{f.added_by ? ` · ${f.added_by}` : ""} · {f.domain_source_id
                    ? <>In the domain&apos;s documents{domainId && <> (<Link className="underline" href={`/os/knowledge/domains/${domainId}`}>review</Link>)</>}</>
                    : "Kept until the domain exists"}
                </div>
              </div>
              {mayAdd && !f.domain_source_id && (
                <button type="button" className="os-btn" disabled={pending || previewing} aria-label={`Remove ${f.file_name}`} onClick={() => remove(f.file_id)}>
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              )}
            </li>
          ))}
        </ul>
      ) : <p className="os-faint text-[12px]">No files yet.{mayAdd ? " Attach what describes the domain, in any format." : ""}</p>}
      {mayAdd && (
        <div className="mt-3 grid gap-2">
          <FilePicker files={picked} onChange={setPicked} disabled={pending || previewing} label="Add files" />
          {picked.length > 0 && (
            <div><button type="button" className="os-btn os-btn-primary" disabled={pending || previewing} onClick={upload} data-testid="upload-request-files">
              {pending ? <Loader2 className="os-spin h-3.5 w-3.5" /> : <Upload className="h-3.5 w-3.5" />} Upload {picked.length} file{picked.length === 1 ? "" : "s"}
            </button></div>
          )}
        </div>
      )}
      {line && <div role={line.fail ? "alert" : "status"} className={cn("os-banner mt-2 text-[12px]", line.fail && "os-banner-red")}>{line.text}</div>}
    </div>
  );
}
