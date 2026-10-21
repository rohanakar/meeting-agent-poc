import { PlusIcon, Trash2Icon } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input, Label, Select } from "@/components/ui/input";
import { type Meeting, request } from "@/lib/api";
import { useConnection } from "@/lib/connection";

export function InviteDialog() {
  const connection = useConnection();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [meetingUrl, setMeetingUrl] = useState("");
  const [title, setTitle] = useState("");
  const [when, setWhen] = useState<"now" | "later">("now");
  const [joinAt, setJoinAt] = useState("");
  const [egressMode, setEgressMode] = useState("");
  const [metadata, setMetadata] = useState<{ key: string; value: string }[]>([]);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSending(true);
    setError("");
    try {
      const meeting = await request<Meeting>(connection, "/api/meetings", {
        method: "POST",
        params: { namespace: undefined },
        body: {
          namespace: connection.namespace,
          meeting_url: meetingUrl,
          title,
          join_at: when === "later" && joinAt ? new Date(joinAt).toISOString() : null,
          egress_mode: egressMode || null,
          metadata: Object.fromEntries(
            metadata.filter((row) => row.key.trim()).map((row) => [row.key.trim(), row.value]),
          ),
        },
      });
      setOpen(false);
      navigate(`/meetings/${meeting.id}`);
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setSending(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <PlusIcon /> Invite agent
        </Button>
      </DialogTrigger>
      <DialogContent>
        <div className="space-y-1.5">
          <DialogTitle className="text-lg font-semibold">Invite the agent to a meeting</DialogTitle>
          <DialogDescription className="text-sm text-muted-foreground">
            The agent joins, tells everyone that it records, and writes a summary afterwards. Use
            it only when all participants agree to the recording.
          </DialogDescription>
        </div>
        <form className="grid gap-4" onSubmit={submit}>
          <div className="grid gap-2">
            <Label htmlFor="meeting-url">Meeting link</Label>
            <Input
              id="meeting-url"
              required
              placeholder="https://meet.google.com/abc-defg-hij or https://zoom.us/j/…"
              value={meetingUrl}
              onChange={(event) => setMeetingUrl(event.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="title">Title</Label>
            <Input
              id="title"
              required
              value={title}
              onChange={(event) => setTitle(event.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="when">Join</Label>
            <div className="flex gap-2">
              <Select
                id="when"
                className="w-32"
                value={when}
                onChange={(event) => setWhen(event.target.value as "now" | "later")}
              >
                <option value="now">Now</option>
                <option value="later">At</option>
              </Select>
              {when === "later" && (
                <Input
                  type="datetime-local"
                  required
                  aria-label="Join time"
                  value={joinAt}
                  onChange={(event) => setJoinAt(event.target.value)}
                />
              )}
            </div>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="egress">Network egress</Label>
            <Select
              id="egress"
              value={egressMode}
              onChange={(event) => setEgressMode(event.target.value)}
            >
              <option value="">Server default</option>
              <option value="direct">Direct</option>
              <option value="proxy">Proxy</option>
            </Select>
          </div>
          <div className="grid gap-2">
            <div className="flex items-center justify-between">
              <Label>Metadata</Label>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setMetadata([...metadata, { key: "", value: "" }])}
              >
                <PlusIcon /> Add
              </Button>
            </div>
            {metadata.map((row, index) => (
              <div key={index} className="flex gap-2">
                <Input
                  aria-label="Key"
                  placeholder="matter"
                  value={row.key}
                  onChange={(event) =>
                    setMetadata(metadata.map((r, i) => (i === index ? { ...r, key: event.target.value } : r)))
                  }
                />
                <Input
                  aria-label="Value"
                  placeholder="M-1042"
                  value={row.value}
                  onChange={(event) =>
                    setMetadata(metadata.map((r, i) => (i === index ? { ...r, value: event.target.value } : r)))
                  }
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label="Remove"
                  onClick={() => setMetadata(metadata.filter((_, i) => i !== index))}
                >
                  <Trash2Icon />
                </Button>
              </div>
            ))}
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={sending}>
              {sending ? "Inviting…" : "Invite"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
