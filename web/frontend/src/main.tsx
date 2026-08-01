import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";
import { RuntimeTasksProvider } from "./context/RuntimeTasksContext";
import "./styles/global.css";
import "./styles/pages.css";
import "./styles/graph.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RuntimeTasksProvider>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </RuntimeTasksProvider>
  </React.StrictMode>,
);
