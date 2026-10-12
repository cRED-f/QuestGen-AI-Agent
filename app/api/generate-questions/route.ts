import { NextResponse } from "next/server";

// Deprecated Phase 0 endpoint: it previously accepted provider secrets in
// query strings. The replacement Phase 1 workspace uses POST-only model calls
// through the local Python backend with server-managed credentials.
const deprecated = () => NextResponse.json(
  { message: "Legacy generation has been disabled. Use the QuestGen chat workspace." },
  { status: 410 },
);

export const GET = deprecated;
export const POST = deprecated;
