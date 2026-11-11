import { api } from "./api";
import { SystemStatus } from "./components/SystemStatus";
import { Logo } from "./components/ui";
import { Link, usePath, usePoll } from "./lib";
import { Home } from "./pages/Home";
import { JobPage } from "./pages/JobPage";

export default function App() {
  const path = usePath();
  const { data: system, error } = usePoll(api.system, () => 30000, "system");
  const jobMatch = path.match(/^\/jobs\/([^/]+)\/?$/);

  return (
    <div className="flex min-h-dvh flex-col">
      <nav className="sticky top-0 z-30 border-b border-line bg-bg/75 backdrop-blur-xl">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4 sm:px-6">
          <Link to="/" className="rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-accent">
            <Logo />
          </Link>
          <SystemStatus system={system} error={error} />
        </div>
      </nav>

      <main className="flex-1">
        {jobMatch ? <JobPage key={jobMatch[1]} id={decodeURIComponent(jobMatch[1])} /> : <Home system={system} />}
      </main>

      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-2 px-4 py-5 text-xs text-faint sm:px-6">
          <span>Video and audio are processed on this machine.</span>
          <span className="font-mono">v0.1 · milestone 1</span>
        </div>
      </footer>
    </div>
  );
}
