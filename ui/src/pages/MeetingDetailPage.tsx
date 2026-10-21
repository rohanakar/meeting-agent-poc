import { SquareIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import { StatusBadge } from "@/components/StatusBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { type MeetingDetail, request, TERMINAL_STATUSES } from "@/lib/api";
import { useConnection } from "@/lib/connection";
import { cn, formatDate, formatTimestamp } from "@/lib/utils";

function Fact({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm break-all">{value || "—"}</dd>
    </div>
  );
}

export default function MeetingDetailPage() {
  const { meetingId } = useParams();
  const [searchParams] = useSearchParams();
  const connection = useConnection();
  const [detail, setDetail] = useState<MeetingDetail | null>(null);
  const [audioUrl, setAudioUrl] = useState("");
  const [error, setError] = useState("");
  const [stopping, setStopping] = useState(false);
  const audio = useRef<HTMLAudioElement>(null);
  const seekTo = Number(searchParams.get("t") ?? NaN);

  const status = detail?.meeting.status;
  const terminal = status ? TERMINAL_STATUSES.includes(status) : false;
  const hasAudio = status === "transcribing" || status === "processing" || status === "done";

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const load = () =>
      request<MeetingDetail>(connection, `/api/meetings/${meetingId}`)
        .then((result) => {
          if (!active) return;
          setDetail(result);
          setError("");
          if (!TERMINAL_STATUSES.includes(result.meeting.status)) timer = setTimeout(load, 5000);
        })
        .catch((problem: Error) => active && setError(problem.message));
    load();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [connection, meetingId]);

  useEffect(() => {
    if (!hasAudio || audioUrl) return;
    // Fetched with the API key header, which an <audio src> cannot send.
    request<Blob>(connection, `/api/meetings/${meetingId}/audio`)
      .then((blob) => setAudioUrl(URL.createObjectURL(blob)))
      .catch(() => undefined);
  }, [connection, meetingId, hasAudio, audioUrl]);

  function play(ms: number) {
    if (!audio.current) return;
    audio.current.currentTime = ms / 1000;
    void audio.current.play();
  }

  async function stop() {
    setStopping(true);
    try {
      await request(connection, `/api/meetings/${meetingId}/stop`, { method: "POST" });
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setStopping(false);
    }
  }

  if (!detail) {
    return <p className="text-sm text-muted-foreground">{error || "Loading…"}</p>;
  }
  const { meeting, events, segments, summary, action_items } = detail;
  const highlighted = Number.isNaN(seekTo)
    ? -1
    : segments.findIndex((segment) => segment.end_ms >= seekTo);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <Link to="/" className="text-sm text-muted-foreground hover:text-foreground">
          ← Meetings
        </Link>
      </div>
      <div className="flex flex-wrap items-start gap-3">
        <div className="mr-auto space-y-1">
          <h1 className="text-2xl font-semibold">{meeting.title}</h1>
          <a href={meeting.meeting_url} className="text-sm text-muted-foreground hover:underline">
            {meeting.meeting_url}
          </a>
        </div>
        <StatusBadge status={meeting.status} />
        {!terminal && (
          <Button variant="outline" size="sm" disabled={stopping} onClick={stop}>
            <SquareIcon /> {meeting.status === "scheduled" ? "Cancel" : "Stop agent"}
          </Button>
        )}
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
      {meeting.failure_reason && (
        <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm">
          {meeting.failure_reason}
        </p>
      )}
      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Fact label="Platform" value={meeting.platform === "google_meet" ? "Google Meet" : "Zoom"} />
        <Fact label="Egress" value={meeting.egress_mode} />
        <Fact label="Exit IP" value={meeting.exit_ip} />
        <Fact label="Admission" value={meeting.admission} />
        <Fact label="Recording started" value={formatDate(meeting.started_at)} />
        <Fact label="Ended" value={formatDate(meeting.ended_at)} />
        <Fact
          label="Attendees"
          value={meeting.attendees
            .map((attendee) => (attendee.email ? `${attendee.name} <${attendee.email}>` : attendee.name))
            .join(", ")}
        />
        <Fact
          label="Metadata"
          value={Object.entries(meeting.metadata)
            .map(([key, value]) => `${key}: ${value}`)
            .join(", ")}
        />
      </dl>

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <Card>
            <CardHeader>
              <CardTitle>Summary</CardTitle>
              {summary && (
                <p className="text-xs text-muted-foreground">
                  AI draft by {summary.model}. Check it against the transcript before you rely on it.
                </p>
              )}
            </CardHeader>
            <CardContent className="space-y-4 text-sm">
              {summary ? (
                <>
                  <p className="leading-relaxed">{summary.summary}</p>
                  {summary.key_points.length > 0 && (
                    <ul className="list-disc space-y-1 pl-5">
                      {summary.key_points.map((point) => (
                        <li key={point}>{point}</li>
                      ))}
                    </ul>
                  )}
                  {summary.decisions.length > 0 && (
                    <div>
                      <h4 className="mb-1 font-medium">Decisions</h4>
                      <ul className="list-disc space-y-1 pl-5">
                        {summary.decisions.map((decision) => (
                          <li key={decision}>{decision}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </>
              ) : (
                <p className="text-muted-foreground">The summary appears after the meeting.</p>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Action items</CardTitle>
            </CardHeader>
            <CardContent>
              {action_items.length ? (
                <ul className="space-y-3 text-sm">
                  {action_items.map((item) => {
                    const cited = segments.find((segment) => item.segment_ids.includes(segment.idx));
                    return (
                      <li key={item.text} className="flex flex-wrap items-baseline gap-2">
                        <span className="mr-auto">{item.text}</span>
                        {item.owner && <Badge variant="secondary">{item.owner}</Badge>}
                        {item.due_date && <Badge variant="outline">{item.due_date}</Badge>}
                        {cited && (
                          <button
                            className="font-mono text-xs text-muted-foreground hover:text-foreground"
                            onClick={() => play(cited.start_ms)}
                          >
                            {formatTimestamp(cited.start_ms)}
                          </button>
                        )}
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="text-sm text-muted-foreground">None yet.</p>
              )}
            </CardContent>
          </Card>
        </div>
        <Card>
          <CardHeader>
            <CardTitle>Timeline</CardTitle>
          </CardHeader>
          <CardContent>
            <ol className="space-y-2 text-sm">
              {events.map((event, index) => (
                <li key={index} className="grid grid-cols-[4.5rem_1fr] gap-2">
                  <span className="font-mono text-xs text-muted-foreground">
                    {new Date(event.at).toLocaleTimeString()}
                  </span>
                  <span>
                    {event.kind.replaceAll("_", " ")}
                    {Object.keys(event.detail).length > 0 && (
                      <span className="block font-mono text-xs break-all text-muted-foreground">
                        {JSON.stringify(event.detail)}
                      </span>
                    )}
                  </span>
                </li>
              ))}
              {events.length === 0 && <li className="text-muted-foreground">Waiting for the bot.</li>}
            </ol>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Transcript</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {audioUrl && (
            <audio
              ref={audio}
              controls
              src={audioUrl}
              className="w-full"
              onLoadedMetadata={() => !Number.isNaN(seekTo) && play(seekTo)}
            />
          )}
          {segments.length ? (
            <ol className="space-y-1 text-sm">
              {segments.map((segment, index) => (
                <li
                  key={segment.idx}
                  className={cn(
                    "grid grid-cols-[3.5rem_11rem_1fr] gap-2 rounded px-1 py-0.5",
                    index === highlighted && "bg-accent",
                  )}
                >
                  <button
                    className="text-left font-mono text-xs text-muted-foreground hover:text-foreground"
                    onClick={() => play(segment.start_ms)}
                  >
                    {formatTimestamp(segment.start_ms)}
                  </button>
                  <span className="truncate font-medium" title={segment.speaker_email ?? undefined}>
                    {segment.speaker_name ?? segment.speaker_label}
                    {segment.speaker_email && (
                      <span className="block truncate text-xs font-normal text-muted-foreground">
                        {segment.speaker_email}
                      </span>
                    )}
                  </span>
                  <span>{segment.text}</span>
                </li>
              ))}
            </ol>
          ) : (
            <p className="text-sm text-muted-foreground">The transcript appears after the meeting.</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
