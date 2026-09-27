import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "../styles/tokens.css";
import "../styles/app.css";
import { Indicator } from "./Indicator";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Indicator />
  </StrictMode>,
);
