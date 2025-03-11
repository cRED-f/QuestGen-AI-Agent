import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "QuestGen",
  description:
    "QuestGen is an AI-powered exam question generator that creates customized question papers from user-uploaded PDFs, offering personalized options for question types, marks, and institutional branding.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>{children}</body>
    </html>
  );
}
