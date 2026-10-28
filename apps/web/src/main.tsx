import React from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "./index.css";
import App from "./App";
import Phone from "./Phone";

const qc = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } });
const isPhone = location.pathname.startsWith("/phone");

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>{isPhone ? <Phone /> : <App />}</QueryClientProvider>
  </React.StrictMode>,
);
