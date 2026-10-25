/*
 * App.tsx: sign-in gate + the admin shell (sidebar, account menu) + the five pages.
 * Pages are picked by the URL hash (#/people, #/agents, ...), so the page's address stays /admin/: the one
 * redirect URI Microsoft knows.
 */
import { useEffect, useState, type ComponentType } from "react";
import { AlertCircle, Bot, BrickWall, Loader2, LogOut, ScrollText, Server, ShieldCheck, Users } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { api, type Firm } from "@/lib/api";
import { finishSignIn, getToken, signIn, signOut } from "@/lib/auth";
import People from "@/pages/People";
import Engagements from "@/pages/Engagements";
import Agents from "@/pages/Agents";
import Servers from "@/pages/Servers";
import Audit from "@/pages/Audit";

const PAGES: { id: string; label: string; icon: ComponentType<{ className?: string }>; page: ComponentType<{ firm: Firm }> }[] = [
  { id: "people", label: "People", icon: Users, page: People },
  { id: "engagements", label: "Engagements & walls", icon: BrickWall, page: Engagements },
  { id: "agents", label: "Agents", icon: Bot, page: Agents },
  { id: "servers", label: "Tool servers", icon: Server, page: Servers },
  { id: "audit", label: "Audit log", icon: ScrollText, page: Audit },
];

const pageFromHash = () => PAGES.find((p) => `#/${p.id}` === location.hash)?.id ?? "people";

// Light / dark follows the system (no inline script, so the page's CSP stays strict).
function useSystemTheme() {
  useEffect(() => {
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const apply = () => document.documentElement.classList.toggle("dark", mq.matches);
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, []);
}

function Logo({ className }: { className?: string }) {
  return (
    <div className={cn("flex items-center gap-2.5", className)}>
      <div className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground"><ShieldCheck className="size-4.5" /></div>
      <span className="text-[15px] font-semibold tracking-tight">Warden</span>
    </div>
  );
}

function SignInScreen({ error, busy }: { error: string | null; busy: boolean }) {
  return (
    <div className="flex min-h-svh items-center justify-center bg-muted/40 p-4">
      <Card className="w-full max-w-sm">
        <CardHeader className="items-center text-center">
          <Logo className="mb-2 justify-center" />
          <CardTitle className="text-xl">Admin sign-in</CardTitle>
          <CardDescription>For your firm's Warden admins. Sign in with your work Microsoft account.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          {error && <Alert variant="destructive"><AlertCircle /><AlertTitle>Sign-in didn't work</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
          <Button size="lg" disabled={busy} onClick={() => signIn()}>
            {busy ? <Loader2 className="animate-spin" /> : <MicrosoftMark />}Sign in with Microsoft
          </Button>
          <p className="text-center text-xs text-muted-foreground">Needs the <span className="font-medium">Warden Admin</span> role in Microsoft Entra ID.</p>
        </CardContent>
      </Card>
    </div>
  );
}

function MicrosoftMark() {
  return (
    <span className="grid grid-cols-2 gap-px" aria-hidden>
      {["#f25022", "#7fba00", "#00a4ef", "#ffb900"].map((c) => <span key={c} className="size-[7px]" style={{ background: c }} />)}
    </span>
  );
}

function Shell({ firm }: { firm: Firm }) {
  const [page, setPage] = useState(pageFromHash);
  useEffect(() => {
    const on = () => setPage(pageFromHash());
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  const Page = PAGES.find((p) => p.id === page)!.page;
  const initials = firm.admin.split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase();

  const nav = (compact: boolean) => PAGES.map((p) => (
    <a key={p.id} href={`#/${p.id}`} aria-current={page === p.id ? "page" : undefined}
      className={cn("flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground",
        page === p.id && "bg-accent text-foreground", compact && "whitespace-nowrap")}>
      <p.icon className="size-4" />{p.label}
    </a>
  ));

  return (
    <div className="flex min-h-svh bg-background">
      <aside className="sticky top-0 hidden h-svh w-64 shrink-0 flex-col border-r bg-sidebar p-3 md:flex">
        <Logo className="px-2 py-2" />
        <div className="mx-2 mt-4 mb-2 rounded-lg border bg-background px-3 py-2">
          <p className="text-xs text-muted-foreground">Firm</p>
          <p className="truncate text-sm font-medium">{firm.name}</p>
        </div>
        <nav className="mt-2 grid gap-0.5">{nav(false)}</nav>
        <p className="mt-auto px-2.5 pb-1 text-xs leading-relaxed text-muted-foreground">Every change applies from agents' next call and is recorded in the audit log.</p>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 flex h-14 items-center gap-3 border-b bg-background/85 px-4 backdrop-blur md:px-8">
          <Logo className="md:hidden" />
          <span className="hidden text-sm text-muted-foreground md:inline">{firm.name}</span>
          <span className="hidden text-sm text-muted-foreground md:inline">/</span>
          <span className="hidden text-sm font-medium md:inline">{PAGES.find((p) => p.id === page)!.label}</span>
          <div className="ml-auto">
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button variant="ghost" className="h-9 gap-2 px-2" />}>
                <Avatar className="size-7"><AvatarFallback className="text-xs">{initials}</AvatarFallback></Avatar>
                <span className="hidden text-sm sm:inline">{firm.admin}</span>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56">
                <DropdownMenuGroup>
                  <DropdownMenuLabel><p className="text-sm font-medium">{firm.admin}</p><p className="text-xs font-normal text-muted-foreground">Warden admin · {firm.name}</p></DropdownMenuLabel>
                </DropdownMenuGroup>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={() => { signOut(); location.reload(); }}><LogOut />Sign out</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </header>
        <nav className="flex gap-1 overflow-x-auto border-b px-3 py-2 md:hidden">{nav(true)}</nav>
        <main className="mx-auto w-full max-w-6xl flex-1 p-4 md:p-8"><Page firm={firm} /></main>
      </div>
    </div>
  );
}

export default function App() {
  useSystemTheme();
  const [firm, setFirm] = useState<Firm | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        await finishSignIn();
        if (getToken()) setFirm(await api<Firm>("GET", "/admin/firm"));
      } catch (e) {
        signOut();
        setError((e as Error).message);
      } finally {
        setChecking(false);
      }
    })();
  }, []);

  return (
    <TooltipProvider>
      {firm ? <Shell firm={firm} /> : <SignInScreen error={error} busy={checking} />}
      <Toaster position="bottom-right" richColors />
    </TooltipProvider>
  );
}
