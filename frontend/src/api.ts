import type { VesselModel } from "./types/vessel-model";
import type { Parcel, StabilityResult, TankLoad } from "./types/api";

async function call<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (j.detail) detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch { /* keep status text */ }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => call<{ status: string; modelConfigured: boolean }>("/health"),
  vessel: (id: string) => call<VesselModel>(`/vessels/${id}`),
  check: (vessel_id: string, loads: TankLoad[]) =>
    call<StabilityResult>("/stability/check", { vessel_id, loads }),
  parseOrder: (text: string) => call<{ parcels: Parcel[]; notes: string[] }>("/ai/parse-order", { text }),
  explain: (result: StabilityResult) => call<{ explanation: string }>("/ai/explain", { result }),
};
