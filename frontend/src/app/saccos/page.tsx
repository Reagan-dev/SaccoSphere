"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch } from "@/lib/api";

type Sacco = {
  id: string;
  name: string;
  description: string;
  is_member: boolean;
};

export default function SaccoListPage() {
  const router = useRouter();

  const [saccos, setSaccos] = useState<Sacco[]>([]);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    loadSaccos();
  }, [page, search]);

  async function loadSaccos() {
    setLoading(true);

    const res = await apiFetch(
      `/saccos/?page=${page}&search=${encodeURIComponent(search)}`
    );

    if (res.ok) {
      const data = await res.json();
      setSaccos(data.results);
      setTotalPages(data.total_pages);
    }

    setLoading(false);
  }

  function enterSacco(sacco: Sacco) {
    if (sacco.is_member) {
      router.push(`/saccos/${sacco.id}/dashboard`);
    } else {
      router.push(`/saccos/${sacco.id}/auth`);
    }
  }

  return (
    <div className="min-h-screen bg-gray-50 px-4 py-6">
      <div className="max-w-3xl mx-auto">
        {/* Header */}
        <h1 className="text-2xl font-semibold mb-4">Select a SACCO</h1>

        {/* Search */}
        <input
          type="text"
          placeholder="Search SACCOs…"
          className="w-full mb-6 rounded-lg border px-4 py-3"
          value={search}
          onChange={(e) => {
            setPage(1);
            setSearch(e.target.value);
          }}
        />

        {/* List */}
        <div className="space-y-4">
          {loading && <p className="text-gray-500">Loading…</p>}

          {!loading &&
            saccos.map((sacco) => (
              <div
                key={sacco.id}
                className="rounded-xl bg-white p-5 shadow-sm flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4"
              >
                <div>
                  <h2 className="text-lg font-medium">{sacco.name}</h2>
                  <p className="text-sm text-gray-600">
                    {sacco.description}
                  </p>

                  <span
                    className={`inline-block mt-2 text-xs font-medium px-2 py-1 rounded-full ${
                      sacco.is_member
                        ? "bg-green-100 text-green-700"
                        : "bg-yellow-100 text-yellow-700"
                    }`}
                  >
                    {sacco.is_member ? "Member" : "Not a member"}
                  </span>
                </div>

                <button
                  onClick={() => enterSacco(sacco)}
                  className="rounded-lg px-4 py-2 bg-blue-600 text-white font-medium"
                >
                  {sacco.is_member ? "Open Dashboard" : "Join / Login"}
                </button>
              </div>
            ))}

          {!loading && saccos.length === 0 && (
            <p className="text-gray-500 text-center">
              No SACCOs found
            </p>
          )}
        </div>

        {/* Pagination */}
        <div className="flex justify-between items-center mt-8">
          <button
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
            className="px-4 py-2 rounded-lg border disabled:opacity-40"
          >
            Previous
          </button>

          <span className="text-sm text-gray-600">
            Page {page} of {totalPages}
          </span>

          <button
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
            className="px-4 py-2 rounded-lg border disabled:opacity-40"
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}
