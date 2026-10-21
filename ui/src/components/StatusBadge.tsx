import { Badge } from "@/components/ui/badge";
import type { MeetingStatus } from "@/lib/api";

const LIVE: MeetingStatus[] = ["joining", "waiting", "in_meeting", "transcribing", "processing"];

export function StatusBadge({ status }: { status: MeetingStatus }) {
  if (status === "failed") return <Badge variant="destructive">failed</Badge>;
  if (status === "done") return <Badge>done</Badge>;
  if (LIVE.includes(status)) {
    return (
      <Badge variant="secondary" className="gap-1.5">
        <span className="size-1.5 animate-pulse rounded-full bg-current" />
        {status.replace("_", " ")}
      </Badge>
    );
  }
  return <Badge variant="outline">{status}</Badge>;
}
