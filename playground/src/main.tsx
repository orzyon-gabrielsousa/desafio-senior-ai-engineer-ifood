import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/nunito-sans/latin-400.css";
import "@fontsource/nunito-sans/latin-600.css";
import "@fontsource/nunito-sans/latin-700.css";
import "@fontsource/nunito-sans/latin-800.css";
import { App } from "./App";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("Não achei o elemento #root.");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
