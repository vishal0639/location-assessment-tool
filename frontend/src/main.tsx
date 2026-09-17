import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Link, Route, Routes } from "react-router-dom";
import ListPage from "./pages/ListPage";
import DetailPage from "./pages/DetailPage";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <header>
        <Link to="/">Location Assessments</Link>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<ListPage />} />
          <Route path="/assessments/:id" element={<DetailPage />} />
        </Routes>
      </main>
    </BrowserRouter>
  </React.StrictMode>,
);
