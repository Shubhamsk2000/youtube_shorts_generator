import { useEffect } from "react";
import { Compass } from "lucide-react";
import { ConfirmProvider, ErrorBoundary, ToastProvider } from "@kit/feedback";
import { Link, navigate, usePath, usePoll } from "@kit/lib";
import { Empty } from "@kit/ui";
import { studio, type Status } from "./app/api";
import { Footer, Nav, type Section } from "./app/Shell";
import { SettingsPage } from "./app/SettingsPage";
import { SystemPage } from "./app/SystemPage";
import { Home } from "./shorts/pages/Home";
import { JobPage } from "./shorts/pages/JobPage";
import { EditorPage } from "./shorts/pages/EditorPage";

type Route = { section: Section; title: string; key: string; page: () => React.ReactNode };

/** Old addresses (bookmarks, the removed anime app) -> current ones. */
function legacy(path: string): string | null {
  let m: RegExpMatchArray | null;
  if ((m = path.match(/^\/shorts\/jobs\/([^/]+)\/?$/))) return `/jobs/${m[1]}`;
  if (path === "/shorts" || path === "/shorts/" || path.startsWith("/anime")) return "/";
  if (path.startsWith("/settings/channel")) return "/settings?tab=card";
  return null;
}

export default function App() {
  const path = usePath();
  // one slow or failed check is not "offline": the banner waits for two in a row
  const { data: status, failing } = usePoll(studio.status, (s) => (s?.queue.current ? 2000 : 8000), "status");
  const to = legacy(path);
  useEffect(() => { if (to) navigate(to, { replace: true }); }, [to]);
  const route = resolve(path, status);

  useEffect(() => {
    document.title = route.title ? `${route.title} · Shorts Studio` : "Shorts Studio";
  }, [route.title]);

  return (
    <ToastProvider>
      <ConfirmProvider>
        <div className="flex min-h-dvh flex-col">
          <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-lg focus:bg-surface focus:px-3 focus:py-2">
            Skip to content
          </a>
          <Nav section={route.section} status={status} offline={failing >= 2} />
          <main id="main" className="flex-1">
            <ErrorBoundary label="This page" resetKey={route.key}>{route.page()}</ErrorBoundary>
          </main>
          <Footer status={status} />
        </div>
      </ConfirmProvider>
    </ToastProvider>
  );
}

function resolve(path: string, status: Status | undefined): Route {
  let m: RegExpMatchArray | null;
  if (path === "/" || path === "") return { section: "projects", title: "", key: "home", page: () => <Home status={status} /> };
  if ((m = path.match(/^\/jobs\/([^/]+)\/clips\/([^/]+)\/?$/))) {
    const id = decodeURIComponent(m[1]);
    const cid = decodeURIComponent(m[2]);
    return { section: "projects", title: "Edit clip", key: `edit-${id}-${cid}`, page: () => <EditorPage key={`${id}/${cid}`} jobId={id} clipId={cid} /> };
  }
  if ((m = path.match(/^\/jobs\/([^/]+)\/?$/))) {
    const id = decodeURIComponent(m[1]);
    return { section: "projects", title: "Project", key: `job-${id}`, page: () => <JobPage key={id} id={id} /> };
  }
  if (path.startsWith("/settings")) return { section: "settings", title: "Settings", key: "settings", page: () => <SettingsPage /> };
  if (path.startsWith("/system")) return { section: "system", title: "System", key: "system", page: () => <SystemPage status={status} /> };
  if (legacy(path)) return { section: "none", title: "", key: "redirect", page: () => null };
  return { section: "none", title: "Not found", key: "404", page: () => <NotFound /> };
}

function NotFound() {
  return (
    <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
      <Empty icon={<Compass className="size-5" />} title="This page doesn't exist">
        <Link to="/" className="font-medium text-accent hover:underline">Back to projects</Link>
      </Empty>
    </div>
  );
}
