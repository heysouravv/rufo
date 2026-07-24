"use client";

import { useState } from "react";
import { useAuth, OrganizationSwitcher } from "@clerk/nextjs";

export default function DevicePage() {
  const { getToken, orgId } = useAuth();
  const [code, setCode] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "approved" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  async function approve(e: React.FormEvent) {
    e.preventDefault();
    setStatus("loading");
    setError(null);
    try {
      const token = await getToken();
      const resp = await fetch("/api/auth/device/approve", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ user_code: code.trim().toUpperCase() }),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail ?? `Request failed (${resp.status})`);
      }
      setStatus("approved");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
      setStatus("error");
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-sm flex-1 flex-col items-center justify-center gap-6 px-6 py-16">
      <span className="text-lg font-semibold tracking-tight text-black">Rufo</span>
      <OrganizationSwitcher />

      {status === "approved" ? (
        <p className="text-center text-sm text-black">
          Device approved. You can close this tab and return to your terminal.
        </p>
      ) : (
        <>
          <p className="text-center text-sm text-black/60">
            Enter the code shown by <code className="font-mono">rufo login</code>
          </p>
          <form onSubmit={approve} className="flex w-full flex-col gap-3">
            <input
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="XXXX-XXXX"
              className="rounded-lg border border-black px-4 py-2 text-center font-mono text-lg tracking-widest uppercase"
              maxLength={9}
              autoFocus
            />
            <button
              type="submit"
              disabled={!orgId || status === "loading" || code.length < 9}
              className="rounded-lg bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
            >
              {status === "loading" ? "Approving…" : "Approve"}
            </button>
          </form>
          {!orgId && (
            <p className="text-center text-sm text-black/60">
              Select or create an organization above before approving.
            </p>
          )}
          {error && <p className="text-center text-sm text-black">{error}</p>}
        </>
      )}
    </div>
  );
}
