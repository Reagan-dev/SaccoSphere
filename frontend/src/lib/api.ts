import { useAuthStore } from "@/store/auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL!;

let isRefreshing = false;

export async function apiFetch(
  path: string,
  options: RequestInit = {},
  retry = true
): Promise<Response> {
  const res = await fetch(`${API_URL}${path}`, {
    ...options,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });

  if (res.status !== 401 || !retry) {
    return res;
  }

  // prevent refresh stampede
  if (isRefreshing) {
    return res;
  }

  isRefreshing = true;

  const refresh = await fetch(`${API_URL}/auth/refresh/`, {
    method: "POST",
    credentials: "include",
  });

  isRefreshing = false;

  if (refresh.ok) {
    return apiFetch(path, options, false);
  }

  // hard failure → clear auth state
  useAuthStore.getState().clear();

  return res;
}
