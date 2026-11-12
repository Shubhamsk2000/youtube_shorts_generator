import { useEffect } from "react";
import { Compass } from "lucide-react";
import { Link, usePath, usePoll } from "@kit/lib";
import { Empty } from "@kit/ui";
import { studio } from "./app/api";
import { Dashboard } from "./app/Dashboard";
import { Footer, Nav, type Section } from "./app/Shell";
import { Channel } from "./anime/pages/Channel";
import { AnimeHome } from "./anime/pages/Home";
import { AnimeJobPage } from "./anime/pages/JobPage";
import { MusicPage } from "./anime/pages/Music";
import { Home as ShortsHome } from "./shorts/pages/Home";
import { JobPage as ShortsJobPage } from "./shorts/pages/JobPage";
import { api as shortsApi } from "./shorts/api";

type Route = { section: Section; title: string; page: () => React.ReactNode };

export default function App() {
  const path = usePath();
  const { data: status, error } = usePoll(studio.status, (s) => (s?.queue.current ? 2000 : 8000), "status");
  const route = resolve(path, status);

  useEffect(() => { document.title = route.title === "Studio" ? "Studio" : `${route.title} · Studio`; }, [route.title]);

  return (
    <div className="flex min-h-dvh flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-lg focus:bg-surface focus:px-3 focus:py-2">
        Skip to content
      </a>
      <Nav section={route.section} status={status} offline={!!error && !status} />
      <main id="main" className="flex-1">{route.page()}</main>
      <Footer status={status} offline={!!error} />
    </div>
  );
}

function resolve(path: string, status: ReturnType<typeof studio.status> extends Promise<infer S> ? S | undefined : never): Route {
  let m: RegExpMatchArray | null;
  if (path === "/" || path === "") return { section: "home", title: "Studio", page: () => <Dashboard status={status} /> };
  if (path === "/shorts" || path === "/shorts/") return { section: "shorts", title: "Shorts", page: () => <ShortsHomePage /> };
  if ((m = path.match(/^\/shorts\/jobs\/([^/]+)\/?$/)))
    return { section: "shorts", title: "Shorts", page: () => <ShortsJobPage key={m![1]} id={decodeURIComponent(m![1])} /> };
  if (path === "/anime" || path === "/anime/") return { section: "anime", title: "Anime edits", page: () => <AnimeHome /> };
  if (path.startsWith("/anime/music")) return { section: "music", title: "Music library", page: () => <MusicPage /> };
  if ((m = path.match(/^\/anime\/jobs\/([^/]+)\/?$/)))
    return { section: "anime", title: "Anime edits", page: () => <AnimeJobPage key={m![1]} id={decodeURIComponent(m![1])} /> };
  if (path.startsWith("/settings/channel")) return { section: "channel", title: "Channel card", page: () => <Channel /> };
  return { section: "home", title: "Not found", page: () => <NotFound /> };
}

function ShortsHomePage() {
  const { data } = usePoll(shortsApi.system, () => 30000, "shorts-system");
  return <ShortsHome system={data} />;
}

function NotFound() {
  return (
    <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
      <Empty icon={<Compass className="size-5" />} title="This page doesn't exist">
        <Link to="/" className="font-medium text-accent hover:underline">Go to Studio home</Link>
      </Empty>
    </div>
  );
}
