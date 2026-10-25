import { useEffect, useState } from "react";
import { href, useRoute } from "./router";
import { Onboarding } from "./pages/Onboarding";
import { QueueDetail, Queues } from "./pages/Queues";
import { Tracker } from "./pages/Tracker";
import { Resumes } from "./pages/Resumes";
import { Backends } from "./pages/Backends";
import { Architecture } from "./pages/Architecture";
import { Settings } from "./pages/Settings";

const NAV: [string, string][] = [
  ["onboarding", "Get started"],
  ["queues", "Queues"],
  ["tracker", "Tracker"],
  ["resumes", "Resumes"],
  ["backends", "AI backends"],
  ["architecture", "How it works"],
  ["settings", "Settings"],
];

export function App() {
  const [section = "onboarding", id] = useRoute();
  const [version, setVersion] = useState("");
  useEffect(() => {
    window.applyqueue?.appInfo().then((i) => setVersion(`v${i.version} · ${i.platform}`)).catch(() => setVersion(""));
  }, []);

  let page;
  switch (section) {
    case "queues":
      page = id ? <QueueDetail id={id} /> : <Queues />;
      break;
    case "tracker":
      page = <Tracker />;
      break;
    case "resumes":
      page = <Resumes />;
      break;
    case "backends":
      page = <Backends />;
      break;
    case "architecture":
      page = <Architecture />;
      break;
    case "settings":
      page = <Settings />;
      break;
    default:
      page = <Onboarding />;
  }

  return (
    <div className="shell">
      <nav className="sidebar" aria-label="Main">
        <div className="brand">ApplyQueue</div>
        {NAV.map(([key, label]) => (
          <a key={key} href={href(key)} className={key === section ? "active" : ""}>
            {label}
          </a>
        ))}
        <div className="foot">
          <span className="badge">pre-alpha</span>
          <small>{version}</small>
        </div>
      </nav>
      <main className="content">{page}</main>
    </div>
  );
}
