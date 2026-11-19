import { dashboardAuthConfigured } from "@/lib/dashboard-auth";

type LoginPageProps = {
  searchParams: Promise<{ next?: string; error?: string }>;
};

export default async function LoginPage({ searchParams }: LoginPageProps) {
  const params = await searchParams;
  const next = params.next?.startsWith("/") && !params.next.startsWith("//") ? params.next : "/";
  const configured = dashboardAuthConfigured();

  return (
    <main className="flex min-h-screen items-center justify-center bg-surface px-5 py-10">
      <section className="w-full max-w-md rounded-2xl border border-border bg-card p-8 shadow-sm">
        <div className="mb-7">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-brand">BLACK SPIDER</p>
          <h1 className="mt-2 text-2xl font-semibold text-ink">Sign in to SocialScheduler</h1>
          <p className="mt-2 text-sm text-muted">This dashboard is private. Sign in to manage your channels and publishing queue.</p>
        </div>

        {!configured ? (
          <div role="alert" className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-sm text-ink">
            Dashboard login is not configured yet. Before deploying this version, set
            {" "}DASHBOARD_USERNAME, DASHBOARD_PASSWORD, and DASHBOARD_SESSION_SECRET
            {" "}in the server environment. The session secret must contain at least 32 characters.
          </div>
        ) : (
          <form action="/api/auth/login" method="post" className="space-y-5">
            <input type="hidden" name="next" value={next} />
            {params.error === "1" ? (
              <p role="alert" className="rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm text-ink">
                Username or password is incorrect.
              </p>
            ) : null}
            <label className="block space-y-1.5">
              <span className="text-sm font-medium text-ink">Username</span>
              <input
                name="username"
                type="text"
                autoComplete="username"
                required
                autoFocus
                className="w-full rounded-lg border border-border bg-surface px-3 py-2.5 text-ink outline-none focus:border-brand"
              />
            </label>
            <label className="block space-y-1.5">
              <span className="text-sm font-medium text-ink">Password</span>
              <input
                name="password"
                type="password"
                autoComplete="current-password"
                required
                className="w-full rounded-lg border border-border bg-surface px-3 py-2.5 text-ink outline-none focus:border-brand"
              />
            </label>
            <button
              type="submit"
              className="w-full rounded-lg bg-brand px-4 py-3 text-sm font-semibold text-white transition-opacity hover:opacity-90"
            >
              Sign in
            </button>
          </form>
        )}
        <p className="mt-7 text-center text-xs text-muted">Authorized owner access only · Session expires after 12 hours</p>
      </section>
    </main>
  );
}
