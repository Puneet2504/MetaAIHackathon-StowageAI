import { useEffect, useRef, useState } from "react";
import { api, session, SESSION_KEY, setSignedOutHandler, type Session } from "./api";
import { resetGrades } from "./grades";
import Icon from "./Icons";
import { Brand } from "./Illustrations";
import Login from "./Login";
import Memory from "./Memory";
import OpenSpace from "./OpenSpace";
import NewShip from "./NewShip";
import Planner from "./Planner";
import ShipLibrary from "./ShipLibrary";
import { useTheme } from "./theme";
import ThemeToggle from "./ThemeToggle";
import VoyageDetail from "./VoyageDetail";
import Voyages from "./Voyages";

export type View =
  | { kind: "library" }
  | { kind: "planner"; id: string; order?: string }
  | { kind: "edit"; id?: string; copyFrom?: string }
  | { kind: "voyages" }
  | { kind: "voyage"; id: string }
  | { kind: "memory" }
  | { kind: "openspace" };

const TABS: { kind: "library" | "voyages" | "openspace" | "memory"; label: string; icon: string; covers: View["kind"][] }[] = [
  { kind: "library", label: "Ships", icon: "ship", covers: ["library", "planner", "edit"] },
  { kind: "voyages", label: "Voyages", icon: "route", covers: ["voyages", "voyage"] },
  { kind: "openspace", label: "Open space", icon: "allocate", covers: ["openspace"] },
  { kind: "memory", label: "Memory", icon: "memory", covers: ["memory"] },
];

export default function App() {
  const [user, setUser] = useState<Session | null>(session.get);
  const [view, setView] = useState<View>({ kind: "library" });
  const [theme, toggleTheme] = useTheme();

  const userRef = useRef(user);
  userRef.current = user;

  // Switch to another session (or none) and start from the ship list, with nothing carried over.
  const switchUser = (next: Session | null) => { resetGrades(); setUser(next); setView({ kind: "library" }); };
  const signOut = () => { session.clear(); switchUser(null); };

  useEffect(() => {
    setSignedOutHandler(() => switchUser(null));
    if (user) api.me().catch(() => { /* a 401 already signed out */ });
    // Signing in or out in another tab changes the stored session; follow it so this tab never acts as the wrong person.
    const onStorage = (e: StorageEvent) => {
      if (e.key !== null && e.key !== SESSION_KEY) return;
      const next = session.get();
      if (next?.token !== userRef.current?.token) switchUser(next);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { window.scrollTo(0, 0); }, [view.kind]);

  if (!user) return (
    <>
      <ThemeToggle theme={theme} onToggle={toggleTheme} className="signin-theme" />
      <Login onSignedIn={(s) => { session.set(s); switchUser(s); }} />
    </>
  );

  return (
    <div className="app">
      <header className="appbar">
        <div className="appbar-inner">
          <button className="brand-button" onClick={() => setView({ kind: "library" })} aria-label="All ships">
            <Brand compact />
          </button>
          <nav className="tabs" aria-label="Sections">
            {TABS.map((t) => (
              <button key={t.kind} className={`tab ${t.covers.includes(view.kind) ? "active" : ""}`}
                aria-current={t.covers.includes(view.kind) ? "page" : undefined}
                onClick={() => setView({ kind: t.kind } as View)}>
                <Icon name={t.icon} size={16} /> <span>{t.label}</span>
              </button>
            ))}
          </nav>
          <nav className="appbar-user" aria-label="Account">
            <ThemeToggle theme={theme} onToggle={toggleTheme} />
            <span className="user-chip"><Icon name="user" size={16} /><span>{user.email}</span></span>
            <button className="appbar-button" onClick={signOut}><Icon name="logout" size={16} /> Sign out</button>
          </nav>
        </div>
      </header>
      <main className="shell" key={user.email}>
        {view.kind === "library" && <ShipLibrary key={user.email} onOpen={setView} />}
        {view.kind === "planner" && (
          <Planner key={`${view.id}-${view.order ?? ""}`} vesselId={view.id} initialOrder={view.order}
            onBack={() => setView({ kind: "library" })}
            onOpenVoyages={() => setView({ kind: "voyages" })}
            onOpenVoyage={(id) => setView({ kind: "voyage", id })} />
        )}
        {view.kind === "edit" && (
          <NewShip key={`${view.id ?? ""}|${view.copyFrom ?? ""}`} editId={view.id} copyFrom={view.copyFrom}
            onCancel={() => setView({ kind: "library" })}
            onSaved={(id) => setView({ kind: "planner", id })} />
        )}
        {view.kind === "voyages" && (
          <Voyages onOpen={(id) => setView({ kind: "voyage", id })} onShips={() => setView({ kind: "library" })} />
        )}
        {view.kind === "voyage" && (
          <VoyageDetail key={view.id} id={view.id} onBack={() => setView({ kind: "voyages" })}
            onPlanAgain={(id, order) => setView({ kind: "planner", id, order })} />
        )}
        {view.kind === "memory" && <Memory />}
        {view.kind === "openspace" && (
          <OpenSpace onPlan={(id, order) => setView({ kind: "planner", id, order })}
            onOpenVoyage={(id) => setView({ kind: "voyage", id })} />
        )}
      </main>
    </div>
  );
}
