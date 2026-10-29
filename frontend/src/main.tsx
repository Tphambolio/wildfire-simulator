import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import AppErrorBoundary from "./components/AppErrorBoundary";
import "./index.css";
import "./styles/ensemble.css";
import "./styles/skill.css";
import "./styles/assets.css";
import "./styles/structures.css";
import "./styles/infotip.css";
import "./styles/about.css";
import "./styles/layout.css";
import "./styles/scorecard.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppErrorBoundary>
      <App />
    </AppErrorBoundary>
  </StrictMode>
);
