import { fetchAPI } from "@/lib/api";
import { MessagesListClient } from "./MessagesListClient";

export const dynamic = "force-dynamic";
export const revalidate = 15;

export default async function MessagesListPage() {
  const messages = await fetchAPI("/v1/messages?limit=50");

  return <MessagesListClient messages={messages ?? []} />;
}
