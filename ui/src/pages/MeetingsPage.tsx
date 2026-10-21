import { useEffect, useState } from "react";
import { Link } from "react-router";

import { InviteDialog } from "@/components/InviteDialog";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Label, Select } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { type MeetingList, request } from "@/lib/api";
import { useConnection } from "@/lib/connection";
import { formatDate } from "@/lib/utils";

const PAGE_SIZE = 20;
const STATUSES = [
  "scheduled", "joining", "waiting", "in_meeting", "transcribing", "processing", "done", "failed",
  "cancelled",
];

export default function MeetingsPage() {
  const connection = useConnection();
  const [status, setStatus] = useState("");
  const [platform, setPlatform] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<MeetingList | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    const load = () =>
      request<MeetingList>(connection, "/api/meetings", {
        params: { status, platform, limit: PAGE_SIZE, offset },
      })
        .then((result) => {
          if (!active) return;
          setPage(result);
          setError("");
        })
        .catch((problem: Error) => active && setError(problem.message));
    load();
    // Bots change state on their own, so the list follows them.
    const timer = setInterval(load, 10_000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [connection, status, platform, offset]);

  const total = page?.total ?? 0;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <h1 className="mr-auto text-2xl font-semibold">Meetings</h1>
        <div className="grid gap-1.5">
          <Label htmlFor="status-filter">Status</Label>
          <Select
            id="status-filter"
            className="w-40"
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setOffset(0);
            }}
          >
            <option value="">All</option>
            {STATUSES.map((value) => (
              <option key={value} value={value}>
                {value.replace("_", " ")}
              </option>
            ))}
          </Select>
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="platform-filter">Platform</Label>
          <Select
            id="platform-filter"
            className="w-40"
            value={platform}
            onChange={(event) => {
              setPlatform(event.target.value);
              setOffset(0);
            }}
          >
            <option value="">All</option>
            <option value="google_meet">Google Meet</option>
            <option value="zoom">Zoom</option>
          </Select>
        </div>
        <InviteDialog />
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
      <Card className="p-2">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Title</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Platform</TableHead>
              <TableHead>Egress</TableHead>
              <TableHead>Identity</TableHead>
              <TableHead>Admission</TableHead>
              <TableHead>Exit IP</TableHead>
              <TableHead>Created</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {page?.items.map((meeting) => (
              <TableRow key={meeting.id}>
                <TableCell className="max-w-64 truncate font-medium">
                  <Link to={`/meetings/${meeting.id}`} className="hover:underline">
                    {meeting.title}
                  </Link>
                </TableCell>
                <TableCell>
                  <StatusBadge status={meeting.status} />
                </TableCell>
                <TableCell>{meeting.platform === "google_meet" ? "Google Meet" : "Zoom"}</TableCell>
                <TableCell>{meeting.egress_mode}</TableCell>
                <TableCell>{meeting.identity.replace("_", " ")}</TableCell>
                <TableCell>{meeting.admission ?? "—"}</TableCell>
                <TableCell className="font-mono text-xs">{meeting.exit_ip ?? "—"}</TableCell>
                <TableCell className="whitespace-nowrap text-muted-foreground">
                  {formatDate(meeting.created_at)}
                </TableCell>
              </TableRow>
            ))}
            {page && page.items.length === 0 && (
              <TableRow>
                <TableCell colSpan={8} className="py-10 text-center text-muted-foreground">
                  No meetings yet. Invite the agent to one.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Card>
      <div className="flex items-center justify-end gap-3 text-sm text-muted-foreground">
        <span>
          {total ? `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)} of ${total}` : "0 meetings"}
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
        >
          Previous
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={offset + PAGE_SIZE >= total}
          onClick={() => setOffset(offset + PAGE_SIZE)}
        >
          Next
        </Button>
      </div>
    </div>
  );
}
