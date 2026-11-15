"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { ShieldCheck, UserRound } from "lucide-react";
import { api, currentUser, DEFAULT_USER, setCurrentUser, type User } from "@/lib/api";
import { AUTH_MODE, account, signOut } from "@/lib/auth";
import { Logo } from "./ui";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/radar", label: "Act this quarter" },
  { href: "/ask", label: "Ask" },
  { href: "/contracts", label: "Contracts" },
  { href: "/review", label: "Review queue" },
];

export function useUser(): string {
  const [user, setUser] = useState<string>("legal_admin");
  useEffect(() => {
    setUser(currentUser());
    const on = (e: Event) => setUser((e as CustomEvent<string>).detail);
    window.addEventListener("cc-user", on);
    return () => window.removeEventListener("cc-user", on);
  }, []);
  return user;
}

export function Nav() {
  const path = usePathname();
  const user = useUser();
  const [users, setUsers] = useState<User[]>([]);
  const [me, setMe] = useState<User | null>(null);
  const [signedInAs, setSignedInAs] = useState("");
  useEffect(() => {
    // ?as=<user> opens the demo already switched to that user (links in the README, screenshots)
    const as = new URLSearchParams(window.location.search).get("as");
    if (AUTH_MODE === "demo" && as && /^[a-z_]{1,32}$/.test(as) && as !== currentUser()) setCurrentUser(as);
    if (AUTH_MODE === "demo") api<User[]>("/users", {}, DEFAULT_USER).then((us) => {
      setUsers(us);
      if (!us.some((u) => u.name === currentUser())) setCurrentUser(DEFAULT_USER);  // a stale or mistyped ?as=
    }).catch(() => setUsers([]));
    else account().then((a) => setSignedInAs(a?.username ?? ""));
  }, []);
  useEffect(() => {
    api<User>("/me").then(setMe).catch(() => setMe(null));
  }, [user]);
  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-background/85 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-6xl items-center gap-3 px-4 sm:gap-6 sm:px-6">
        <Link href="/" className="flex shrink-0 items-center gap-2 font-serif text-lg">
          <Logo />
          <span className="hidden sm:inline">Contract Radar</span>
        </Link>
        <nav className="flex min-w-0 items-center gap-1 overflow-x-auto text-sm [scrollbar-width:none] [mask-image:linear-gradient(to_right,black_80%,transparent)] sm:[mask-image:none]">
          {LINKS.map((l) => (
            <Link key={l.href} href={l.href}
              className={`whitespace-nowrap border-b-2 px-2 py-4 transition sm:px-2.5 ${active(l.href) ? "border-accent font-medium text-foreground" : "border-transparent text-muted hover:text-foreground"}`}>
              {l.label}
            </Link>
          ))}
        </nav>
        {AUTH_MODE !== "demo" ? (
          <div className="ml-auto flex items-center gap-2 rounded-full border border-line bg-surface py-1 pl-3 pr-1 text-xs text-muted">
            <UserRound className="size-3.5" />
            <span className="max-w-48 truncate">{signedInAs || "signing in…"}</span>
            <button onClick={() => signOut()} className="rounded-full bg-surface-2 px-2 py-1 font-medium text-foreground">Sign out</button>
          </div>
        ) : (
        <label className="ml-auto flex shrink-0 items-center gap-2 rounded-full border border-line bg-surface py-1 pl-3 pr-1 text-xs text-muted">
          <UserRound className="size-3.5" />
          <span className="hidden md:inline">Signed in as</span>
          <select value={user} onChange={(e) => setCurrentUser(e.target.value)}
            className="rounded-full bg-surface-2 px-2 py-1 text-xs font-medium text-foreground outline-none">
            {(users.length ? users : [{ name: user } as User]).map((u) => (
              <option key={u.name} value={u.name}>{u.name}</option>
            ))}
          </select>
        </label>
        )}
      </div>
      {me && (
        <div className="border-t border-line bg-surface-2/60">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-1 px-4 py-1.5 text-xs text-muted sm:px-6">
            <span>{me.description}</span>
            <span className="flex items-center gap-1">
              <ShieldCheck className="size-3.5" />
              {me.pii_access ? "sees personal data" : "personal data masked"}
              {" · "}{me.can_review ? "can review fields" : "read-only"}
            </span>
          </div>
        </div>
      )}
    </header>
  );
}
