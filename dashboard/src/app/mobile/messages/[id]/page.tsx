import { getMessageById, markMessageRead } from "@/lib/db";
import { MessageDetailClient } from "./MessageDetailClient";
import { redirect } from "next/navigation";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export default async function MessageDetailPage(
  props: { params: Promise<{ id: string }> }
) {
  // Simply extract the id from params
  const { id } = await props.params;
  const msgId = parseInt(id, 10);

  if (isNaN(msgId)) {
    redirect("/mobile/messages");
  }

  const message = getMessageById(msgId);

  if (!message) {
    redirect("/mobile/messages");
  }

  // Mark as read
  if (!message.is_read) {
    try {
      markMessageRead(msgId);
    } catch { /* ignore */ }
  }

  return <MessageDetailClient message={message} />;
}
