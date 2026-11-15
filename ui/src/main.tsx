import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { HOST } from "./workspace/host";
import { Browser } from "./workspace/Start";
import { Workspace } from "./workspace/Workspace";
import "./styles.css";

// The browser app (web/) has a host page and always shows its start screen, then the workspace.
// `?ui=workspace` (what the native window opens) shows the new three-panel workspace;
// anything else the classic review screen. Kept for this tab, like the token.
function pickUi(): "workspace" | "classic" {
  const asked = new URL(window.location.href).searchParams.get("ui");
  try {
    if (asked) sessionStorage.setItem("taxonymous-ui", asked);
    return (asked ?? sessionStorage.getItem("taxonymous-ui")) === "workspace" ? "workspace" : "classic";
  } catch {
    return asked === "workspace" ? "workspace" : "classic";
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {HOST ? <Browser /> : pickUi() === "workspace" ? <Workspace /> : <App />}
  </StrictMode>,
);
