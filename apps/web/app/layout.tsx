import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Jules Workflow Studio",
  description: "A local-first agent orchestration harness for Jules and future adapters.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
