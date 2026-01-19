import { redirect } from "next/navigation";
import { apiFetch } from "@/lib/api";

export default async function ProtectedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const res = await apiFetch("/auth/me/");

  if (!res.ok) redirect("/login");

  return <>{children}</>;
}
