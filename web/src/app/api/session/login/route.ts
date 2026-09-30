import type { NextRequest } from "next/server";
import { credentialExchange } from "@/lib/session";

export async function POST(req: NextRequest) {
  return credentialExchange(req, "/api/auth/login");
}
