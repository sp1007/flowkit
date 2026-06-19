import { useState } from "react";
import { type Project } from "./api/client";
import StatusPills from "./components/StatusPills";
import ProjectGrid from "./components/ProjectGrid";
import ProjectWorkspace from "./components/ProjectWorkspace";
import SettingsDrawer from "./components/settings/SettingsDrawer";

export default function App() {
  const [open, setOpen] = useState<Project | null>(null);
  const [settings, setSettings] = useState(false);

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-neutral-800 px-6 py-3">
        <button
          onClick={() => setOpen(null)}
          className="flex items-center gap-2 text-lg font-semibold tracking-tight"
        >
          <span className="grid h-7 w-7 place-items-center rounded-lg bg-gradient-to-br from-indigo-500 to-fuchsia-500 text-sm text-white">
            ▶
          </span>
          Flow Studio
        </button>

        <div className="flex items-center gap-3">
          <StatusPills />
          <button
            onClick={() => setSettings(true)}
            title="Settings"
            className="grid h-8 w-8 place-items-center rounded-lg text-neutral-400 hover:bg-neutral-800 hover:text-neutral-200"
          >
            ⚙
          </button>
        </div>
      </header>

      <main className={`flex-1 ${open ? "overflow-hidden" : "overflow-auto"}`}>
        {open ? (
          <ProjectWorkspace project={open} onBack={() => setOpen(null)} />
        ) : (
          <ProjectGrid onOpen={setOpen} />
        )}
      </main>

      {settings && <SettingsDrawer onClose={() => setSettings(false)} />}
    </div>
  );
}
