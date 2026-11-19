import { NextRequest, NextResponse } from "next/server";
import {
  createDashboardSession,
  DASHBOARD_SESSION_COOKIE,
  DASHBOARD_SESSION_TTL_SECONDS,
  dashboardAuthConfigured,
  verifyDashboardCredentials,
} from "@/lib/dashboard-auth";

export const runtime = "nodejs";

function safeNextPath(value: FormDataEntryValue | null): string {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) {
    return "/";
  }
  try {
    const url = new URL(value, "https://dashboard.invalid");
    return url.origin === "https://dashboard.invalid" ? url.pathname + url.search : "/";
  } catch {
    return "/";
  }
}

export async function POST(request: NextRequest) {
  const origin = request.headers.get("origin");
  if (!origin || origin !== request.nextUrl.origin) {
    return NextResponse.json({ error: "Cross-origin request rejected." }, { status: 403 });
  }

  if (!dashboardAuthConfigured()) {
    return NextResponse.json(
      {
        error:
          "Dashboard login is not configured. Set DASHBOARD_USERNAME, DASHBOARD_PASSWORD, and a DASHBOARD_SESSION_SECRET of at least 32 characters.",
      },
      { status: 503 },
    );
  }

  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    return NextResponse.json({ error: "Invalid login form." }, { status: 400 });
  }

  const username = form.get("username");
  const password = form.get("password");
  if (
    typeof username !== "string" ||
    typeof password !== "string" ||
    !verifyDashboardCredentials(username, password)
  ) {
    const url = new URL("/login", request.url);
    url.searchParams.set("error", "1");
    return NextResponse.redirect(url, 303);
  }

  const session = createDashboardSession();
  if (!session) {
    return NextResponse.json({ error: "Dashboard session signing is unavailable." }, { status: 503 });
  }

  const response = NextResponse.redirect(
    new URL(safeNextPath(form.get("next")), request.url),
    303,
  );
  response.headers.set("Cache-Control", "no-store");
  response.cookies.set(DASHBOARD_SESSION_COOKIE, session.token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "strict",
    path: "/",
    maxAge: DASHBOARD_SESSION_TTL_SECONDS,
  });
  return response;
}
