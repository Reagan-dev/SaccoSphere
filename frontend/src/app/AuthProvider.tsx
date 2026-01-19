"use client";

import { ReactNode, useEffect } from "react";
import { apiFetch } from "@/lib/api";
import { useAuthStore } from "@/store/auth";

export default function AuthProvider({
  children,
}: {
  children: ReactNode;
}) {
  const setUser = useAuthStore((s) => s.setUser);
  const setAccessToken = useAuthStore((s) => s.setAccessToken);
  const setInitialized = useAuthStore((s) => s.setInitialized);

  useEffect(() => {
    let cancelled = false;

    const bootstrap = async () => {
      try {
        // 1. Try current access token
        const me = await apiFetch("/auth/me");
        if (me.ok) {
          const data = await me.json();
          if (!cancelled) {
            setUser(data.user);
            setAccessToken(data.accessToken);
            setInitialized(true);
          }
          return;
        }

        // 2. Attempt refresh
        const refresh = await apiFetch("/auth/refresh", {
          method: "POST",
        });

        if (!refresh.ok) throw new Error("Refresh failed");

        const refreshData = await refresh.json();
        setAccessToken(refreshData.accessToken);

        // 3. Retry me
        const meRetry = await apiFetch("/auth/me");
        if (!meRetry.ok) throw new Error("Me retry failed");

        const userData = await meRetry.json();
        if (!cancelled) {
          setUser(userData.user);
          setInitialized(true);
        }
      } catch {
        if (!cancelled) {
          setUser(null);
          setAccessToken(null);
          setInitialized(true);
        }
      }
    };

    bootstrap();
    return () => {
      cancelled = true;
    };
  }, [setUser, setAccessToken, setInitialized]);

  return children;
}
