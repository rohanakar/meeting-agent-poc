import { createContext, useContext } from "react";

import type { Connection } from "./api";

export const ConnectionContext = createContext<Connection>({ apiKey: "", namespace: "" });

export function useConnection() {
  return useContext(ConnectionContext);
}
