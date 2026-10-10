import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";
import "./styles/ensemble.css";
import "./styles/skill.css";
import "./styles/assets.css";
import "./styles/structures.css";
import "./styles/infotip.css";
import "./styles/about.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
