import Link from "next/link";

export default function HomePage() {
  return (
    <main className="min-h-screen flex flex-col items-center justify-center bg-white px-6">
      <div className="w-full max-w-md text-center">
        {/* Logo / Brand */}
        <h1 className="text-3xl font-bold mb-2">Saccosphere</h1>
        <p className="text-gray-600 mb-10">
          All your SACCOs. One secure platform.
        </p>

        {/* Actions */}
        <div className="space-y-4">
          <Link
            href="/login"
            className="block w-full rounded-xl bg-blue-600 py-3 text-white font-medium"
          >
            Login
          </Link>

          <Link
            href="/register"
            className="block w-full rounded-xl border border-gray-300 py-3 font-medium"
          >
            Register
          </Link>
        </div>

        {/* Footer */}
        <p className="text-xs text-gray-400 mt-12">
          Secure • Regulated • Multi-SACCO
        </p>
      </div>
    </main>
  );
}
