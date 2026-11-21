const DEFAULT_PUBLIC_APP_URL = "https://reels-spider-production.up.railway.app";

export function publicAppOrigin(): string {
  const configured = (process.env.PUBLIC_APP_URL || DEFAULT_PUBLIC_APP_URL).trim();
  let url: URL;
  try {
    url = new URL(configured);
  } catch {
    throw new Error("PUBLIC_APP_URL must be a valid public HTTPS URL.");
  }
  if (
    url.protocol !== "https:" ||
    !url.hostname ||
    url.hostname === "0.0.0.0" ||
    url.hostname === "127.0.0.1" ||
    url.hostname === "localhost"
  ) {
    throw new Error("PUBLIC_APP_URL must use the public HTTPS application domain.");
  }
  return url.origin;
}
