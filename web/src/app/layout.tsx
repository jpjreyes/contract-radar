import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { Nav } from "@/components/nav";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: { default: "Contract Copilot", template: "%s · Contract Copilot" },
  description:
    "Ask questions across a contract portfolio with verified citations, per-user permissions, masked personal data, extracted key terms and an audit log.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col bg-background text-foreground">
        <Nav />
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6 sm:py-10">{children}</main>
        <footer className="border-t border-line">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-2 px-4 py-5 text-xs text-faint sm:px-6">
            <span>510 public contracts (CUAD, CC BY 4.0). Users and permissions are a demo; every question is audited.</span>
            <span>Postgres RLS · pgvector · Claude · FastAPI · Next.js</span>
          </div>
        </footer>
      </body>
    </html>
  );
}
