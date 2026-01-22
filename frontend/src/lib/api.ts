const API_URL = process.env.NEXT_PUBLIC_API_URL!;

export async function apiFetch(
  path: string,
  options: RequestInit = {}
) {
  const res = await fetch(`${API_URL}${path}`, {
    ...options,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });

  if (res.status === 401) {
    const refresh = await fetch(`${API_URL}/api/accounts/refresh/`, {
      method: "POST",
      credentials: "include",
    });

    if (refresh.ok) {
      return apiFetch(path, options);
    }
  }

  return res;
}
