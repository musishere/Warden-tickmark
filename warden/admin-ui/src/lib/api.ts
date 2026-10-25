/*
 * api.ts: calls to Warden's /admin/... endpoints (AdminController.cs), their shapes, and a small hook.
 * Every request carries the Microsoft token; 401 = token expired or missing -> sign in again.
 */
import { useCallback, useEffect, useState } from "react";
import { getToken, signIn } from "./auth";

export type Firm = { id: string; name: string; admin: string; roles: string[]; scopes: string[] };
export type Person = {
  id: string; name: string; email: string; active: boolean; entraObjectId: string | null;
  engagements: { id: string; name: string; role: string }[];
};
export type Engagement = {
  id: string; name: string;
  members: { userId: string; name: string; role: string }[];
  walls: { userId: string; name: string; reason: string }[];
};
export type Agent = { id: string; name: string; entraClientId: string; allowedScopes: string };
export type Server = { id: string; name: string; url: string; audience: string; tools: { name: string; scope: string }[] };
export type OfferedTool = { name: string; description: string | null; scope: string | null; takenBy: string | null };
export type AuditRow = {
  id: number; at: string; userId: string; agentId: string | null; engagementId: string | null;
  action: string; scope: string | null; allowed: boolean; reason: string; jti: string | null;
};

export class ApiError extends Error {}

export async function api<T = unknown>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: { Authorization: `Bearer ${getToken()}`, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) {
    sessionStorage.removeItem("warden.token");
    await signIn();
    throw new ApiError("Signing in again…");
  }
  if (res.status === 403) throw new ApiError("You need the Warden Admin role in Microsoft Entra to use this page.");
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new ApiError(data?.reason || `${res.status} ${res.statusText}`);
  return data as T;
}

// GET a path; `reload()` fetches it again (after a change).
export function useData<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    setError(null);
    api<T>("GET", path).then((d) => live && setData(d), (e: Error) => live && setError(e.message));
    return () => { live = false; };
  }, [path, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, reload };
}
