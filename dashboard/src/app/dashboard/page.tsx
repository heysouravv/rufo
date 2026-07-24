"use client";

import { useEffect, useState } from "react";
import { useAuth, UserButton, OrganizationSwitcher } from "@clerk/nextjs";

type Deployment = {
  id: string;
  agent_name: string;
  service_id: string;
  region: string;
  image: string;
  uri: string | null;
  status: string;
  created_at: number;
};

type CostRow = {
  agent_name: string;
  service_id: string;
  window_hours: number;
  billable_instance_seconds: number;
  vcpu_count: number;
  memory_gib: number;
  estimated_cost_usd: number;
};

export default function DashboardPage() {
  const { getToken, orgId } = useAuth();
  const [deployments, setDeployments] = useState<Deployment[] | null>(null);
  const [costs, setCosts] = useState<CostRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!orgId) return;

    async function load() {
      try {
        const token = await getToken();
        const headers = { Authorization: `Bearer ${token}` };

        const [deploymentsRes, costsRes] = await Promise.all([
          fetch("/api/deployments", { headers }),
          fetch("/api/costs?window_hours=720", { headers }),
        ]);

        if (!deploymentsRes.ok || !costsRes.ok) {
          throw new Error(
            `deployments: ${deploymentsRes.status}, costs: ${costsRes.status}`
          );
        }

        setDeployments(await deploymentsRes.json());
        setCosts(await costsRes.json());
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load");
      }
    }

    load();
  }, [orgId, getToken]);

  const costByService = new Map(costs?.map((c) => [c.service_id, c]) ?? []);

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-1 flex-col gap-8 px-6 py-10">
      <header className="flex items-center justify-between border-b border-black pb-4">
        <span className="text-lg font-semibold tracking-tight text-black">
          Rufo
        </span>
        <div className="flex items-center gap-3">
          <OrganizationSwitcher />
          <UserButton />
        </div>
      </header>

      {!orgId && (
        <p className="text-sm text-black/60">
          Select or create an organization to see its deployments.
        </p>
      )}

      {error && (
        <p className="rounded-lg border border-black px-4 py-3 text-sm text-black">
          {error}
        </p>
      )}

      {orgId && !deployments && !error && (
        <p className="text-sm text-black/60">Loading deployments…</p>
      )}

      {deployments && deployments.length === 0 && (
        <p className="text-sm text-black/60">
          No agents deployed yet for this organization.
        </p>
      )}

      {deployments && deployments.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-black">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-black text-xs uppercase tracking-wide text-black/60">
              <tr>
                <th className="px-4 py-3 font-medium">Agent</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">URL</th>
                <th className="px-4 py-3 font-medium text-right">
                  Cost (30d)
                </th>
              </tr>
            </thead>
            <tbody>
              {deployments.map((d) => {
                const cost = costByService.get(d.service_id);
                return (
                  <tr key={d.id} className="border-b border-black/10 last:border-0">
                    <td className="px-4 py-3 font-medium text-black">
                      {d.agent_name}
                    </td>
                    <td className="px-4 py-3">
                      <span className="inline-flex items-center gap-1.5 rounded-full border border-black px-2 py-0.5 text-xs font-medium text-black">
                        <span className="h-1.5 w-1.5 rounded-full bg-black" />
                        {d.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-black/60">
                      {d.uri ?? "—"}
                    </td>
                    <td className="px-4 py-3 text-right font-mono tabular-nums text-black">
                      {cost ? `$${cost.estimated_cost_usd.toFixed(4)}` : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
