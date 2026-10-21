import { useState } from "react";
import { NavLink, Route, Routes } from "react-router";

import { Input, Label } from "@/components/ui/input";
import { type Connection, loadConnection, saveConnection } from "@/lib/api";
import { ConnectionContext } from "@/lib/connection";
import { cn } from "@/lib/utils";
import MeetingDetailPage from "@/pages/MeetingDetailPage";
import MeetingsPage from "@/pages/MeetingsPage";
import SearchPage from "@/pages/SearchPage";

export default function App() {
  const [connection, setConnection] = useState<Connection>(loadConnection);

  function update(change: Partial<Connection>) {
    const next = { ...connection, ...change };
    setConnection(next);
    saveConnection(next);
  }

  const navClass = ({ isActive }: { isActive: boolean }) =>
    cn("text-sm", isActive ? "font-semibold" : "text-muted-foreground hover:text-foreground");

  return (
    <ConnectionContext.Provider value={connection}>
      <header className="border-b">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3">
          <span className="font-semibold">Meeting agent</span>
          <nav className="flex gap-4">
            <NavLink to="/" end className={navClass}>
              Meetings
            </NavLink>
            <NavLink to="/search" className={navClass}>
              Search
            </NavLink>
          </nav>
          <div className="ml-auto flex flex-wrap items-center gap-3">
            <Label htmlFor="namespace">Organisation</Label>
            <Input
              id="namespace"
              className="h-8 w-36"
              value={connection.namespace}
              onChange={(event) => update({ namespace: event.target.value.trim() })}
            />
            <Label htmlFor="api-key">API key</Label>
            <Input
              id="api-key"
              type="password"
              className="h-8 w-36"
              value={connection.apiKey}
              onChange={(event) => update({ apiKey: event.target.value })}
            />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Routes>
          <Route path="/" element={<MeetingsPage />} />
          <Route path="/meetings/:meetingId" element={<MeetingDetailPage />} />
          <Route path="/search" element={<SearchPage />} />
        </Routes>
      </main>
    </ConnectionContext.Provider>
  );
}
