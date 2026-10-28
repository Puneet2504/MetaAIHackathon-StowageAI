import type { VesselModel } from "./types/vessel-model";
import type { ApprovedPlanSummary, DraftLayout, MemoryItem, Parcel, PlanResponse, ReadDrawingOut, StabilityResult,
  StowageConstraints, StowagePlan, TankLoad, UsageSummary, VesselLayout, VesselSummary, VoyageDetail,
  VoyageListItem } from "./types/api";

const SESSION_KEY = "tanker-planner-session";

export interface Session {
  token: string;
  email: string;
}

export const session = {
  get: (): Session | null => {
    try { const s = JSON.parse(localStorage.getItem(SESSION_KEY) ?? "null"); return s?.token ? s : null; }
    catch { return null; }
  },
  set: (s: Session) => { try { localStorage.setItem(SESSION_KEY, JSON.stringify(s)); } catch { /* private mode */ } },
  clear: () => { try { localStorage.removeItem(SESSION_KEY); } catch { /* private mode */ } },
};

// App.tsx registers this so an expired session sends the person back to sign-in.
let onSignedOut: () => void = () => {};
export const setSignedOutHandler = (fn: () => void) => { onSignedOut = fn; };

export type SignInResult =
  | { status: "new_account"; email: string }
  | { token: string; user: { email: string }; created: boolean };

// FastAPI returns validation errors as a list; show their messages in plain text.
function errorText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => String(d?.msg ?? d).replace(/^Value error, /, "")).join("; ");
  return JSON.stringify(detail);
}

async function call<T>(path: string, body?: unknown, method?: string, signal?: AbortSignal): Promise<T> {
  const s = session.get();
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (s) headers["Authorization"] = `Bearer ${s.token}`;
  const res = await fetch(`/api${path}`, {
    method: method ?? (body === undefined ? "GET" : "POST"),
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (j.detail) detail = errorText(j.detail);
    } catch { /* keep status text */ }
    if (res.status === 401 && s && !path.startsWith("/auth/signin")) { session.clear(); onSignedOut(); }
    throw new Error(detail);
  }
  return (res.status === 204 ? undefined : res.json()) as Promise<T>;
}

export const api = {
  health: () => call<{ status: string; modelConfigured: boolean; segmentConfigured: boolean; demoEmail?: string | null }>("/health"),
  signIn: (email: string, password: string, create = false) =>
    call<SignInResult>("/auth/signin", { email, password, create }),
  me: () => call<{ email: string }>("/auth/me"),
  vessels: () => call<VesselSummary[]>("/vessels"),
  vessel: (id: string) => call<VesselModel>(`/vessels/${id}`),
  layout: (id: string) => call<DraftLayout>(`/vessels/${id}/layout`),
  previewVessel: (layout: VesselLayout) => call<VesselModel>("/vessels/preview", { layout }),
  saveVessel: (layout: VesselLayout) => call<VesselModel>("/vessels", { layout }),
  updateVessel: (id: string, layout: VesselLayout) => call<VesselModel>(`/vessels/${id}`, { layout }, "PUT"),
  deleteVessel: (id: string) => call<void>(`/vessels/${id}`, undefined, "DELETE"),
  readDrawing: (text: string, image: string | null, signal?: AbortSignal) =>
    call<ReadDrawingOut>("/ai/read-drawing", { text: text.trim() || null, image }, undefined, signal),
  planVoyage: (body: { vessel_id: string; order_text?: string; parcels?: Parcel[]; instructions?: string;
    constraints?: StowageConstraints; memory_off?: string[] }) => call<PlanResponse>("/stowage/plan", body),
  approvePlan: (body: { vessel_id: string; order_text: string; parcels: Parcel[];
    constraints: StowageConstraints; plan: StowagePlan; explanation?: string | null }) =>
    call<{ id: string }>("/stowage/plans", body),
  voyages: () => call<VoyageListItem[]>("/voyages"),
  voyage: (id: string) => call<VoyageDetail>(`/voyages/${id}`),
  deleteVoyage: (id: string) => call<void>(`/voyages/${id}`, undefined, "DELETE"),
  memory: () => call<MemoryItem[]>("/memory"),
  addMemoryNote: (text: string, vessel_id?: string) => call<MemoryItem>("/memory", { text, vessel_id }),
  patchMemory: (id: string, patch: { text?: string; value?: string | number }) =>
    call<MemoryItem>(`/memory/${id}`, patch, "PATCH"),
  deleteMemory: (id: string) => call<void>(`/memory/${id}`, undefined, "DELETE"),
  clearMemory: () => call<void>("/memory", undefined, "DELETE"),
  usage: () => call<UsageSummary>("/usage"),
  listPlans: (vessel_id: string) => call<ApprovedPlanSummary[]>(`/stowage/plans?vessel_id=${encodeURIComponent(vessel_id)}`),
  check: (vessel_id: string, loads: TankLoad[]) =>
    call<StabilityResult>("/stability/check", { vessel_id, loads }),
  parseOrder: (text: string) => call<{ parcels: Parcel[]; notes: string[] }>("/ai/parse-order", { text }),
  explain: (result: StabilityResult) => call<{ explanation: string; source: "ai" | "plain" }>("/ai/explain", { result }),
};
