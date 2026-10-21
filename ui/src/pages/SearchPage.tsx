import { SearchIcon } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Link } from "react-router";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Label, Select } from "@/components/ui/input";
import { request, type SearchHit } from "@/lib/api";
import { useConnection } from "@/lib/connection";
import { formatDate, formatTimestamp } from "@/lib/utils";

const PAGE_SIZE = 10;
const KIND_LABEL: Record<string, string> = {
  transcript: "Transcript",
  summary: "Summary",
  action_item: "Action item",
};

export default function SearchPage() {
  const connection = useConnection();
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  const [platform, setPlatform] = useState("");
  const [offset, setOffset] = useState(0);
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [error, setError] = useState("");
  const [searching, setSearching] = useState(false);

  async function search(nextOffset: number) {
    setSearching(true);
    setError("");
    try {
      const result = await request<{ items: SearchHit[] }>(connection, "/api/search", {
        params: { q: query, kind, platform, limit: PAGE_SIZE, offset: nextOffset },
      });
      setHits(result.items);
      setOffset(nextOffset);
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setSearching(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void search(0);
  }

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Search meetings</h1>
      <form className="flex flex-wrap items-end gap-3" onSubmit={submit}>
        <div className="grid min-w-64 flex-1 gap-1.5">
          <Label htmlFor="query">Question or words</Label>
          <Input
            id="query"
            required
            placeholder="What did we agree about the budget?"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="kind">In</Label>
          <Select id="kind" className="w-36" value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">Everything</option>
            <option value="transcript">Transcripts</option>
            <option value="summary">Summaries</option>
            <option value="action_item">Action items</option>
          </Select>
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="search-platform">Platform</Label>
          <Select
            id="search-platform"
            className="w-36"
            value={platform}
            onChange={(e) => setPlatform(e.target.value)}
          >
            <option value="">All</option>
            <option value="google_meet">Google Meet</option>
            <option value="zoom">Zoom</option>
          </Select>
        </div>
        <Button type="submit" disabled={searching}>
          <SearchIcon /> Search
        </Button>
      </form>
      {error && <p className="text-sm text-destructive">{error}</p>}
      {hits && hits.length === 0 && (
        <p className="text-sm text-muted-foreground">Nothing found in this organisation.</p>
      )}
      <div className="space-y-3">
        {hits?.map((hit) => (
          <Card key={hit.chunk_id} className="space-y-2 p-4">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Link
                className="font-medium hover:underline"
                to={`/meetings/${hit.meeting_id}${hit.start_ms !== null ? `?t=${hit.start_ms}` : ""}`}
              >
                {hit.meeting_title}
              </Link>
              {hit.start_ms !== null && (
                <span className="font-mono text-xs text-muted-foreground">
                  {formatTimestamp(hit.start_ms)}
                </span>
              )}
              <Badge variant="outline">{KIND_LABEL[hit.kind] ?? hit.kind}</Badge>
              <span className="ml-auto text-xs text-muted-foreground">
                {formatDate(hit.created_at)} · score {hit.score.toFixed(2)}
              </span>
            </div>
            <p className="text-sm whitespace-pre-line text-muted-foreground">{hit.text}</p>
          </Card>
        ))}
      </div>
      {hits && hits.length > 0 && (
        <div className="flex justify-end gap-3">
          <Button
            variant="outline"
            size="sm"
            disabled={offset === 0 || searching}
            onClick={() => search(Math.max(0, offset - PAGE_SIZE))}
          >
            Previous
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={hits.length < PAGE_SIZE || searching}
            onClick={() => search(offset + PAGE_SIZE)}
          >
            Next
          </Button>
        </div>
      )}
    </div>
  );
}
