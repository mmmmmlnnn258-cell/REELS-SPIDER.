import "server-only";
import Database from "better-sqlite3";
import fs from "node:fs";
import path from "node:path";
import { config } from "./config";

/*
  Single shared SQLite connection for the dashboard's server-side code.
  better-sqlite3 is synchronous, which is ideal inside route handlers / server
  components — no await juggling. migrate.py enables persistent WAL before both processes
  start; foreign keys are enabled on this connection.
*/

export const SQLITE_BUSY_TIMEOUT_MS = 30_000;

let _db: Database.Database | null = null;

export function getDb(): Database.Database {
  if (_db) return _db;
  // The database directory may not exist on a fresh Railway deployment.
  // Create it only when the first request opens SQLite, never during prerender.
  fs.mkdirSync(path.dirname(config.databasePath), { recursive: true });
  const db = new Database(config.databasePath);
  // The Python worker and dashboard share this SQLite file. WAL permits concurrent
  // readers, but writers still serialize; wait for short worker/dashboard writes
  // instead of failing with SQLITE_BUSY after better-sqlite3's shorter default.
  db.pragma(`busy_timeout = ${SQLITE_BUSY_TIMEOUT_MS}`);
  db.pragma("foreign_keys = ON");
  _db = db;
  return db;
}

export function nowIso(): string {
  return new Date().toISOString();
}
