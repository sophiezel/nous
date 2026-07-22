import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "天工 Dashboard",
  description: "量化投研数据平台",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `
              (function() {
                var theme = localStorage.getItem('quant-theme');
                if (!theme) {
                  theme = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
                }
                if (theme === 'dark') {
                  document.documentElement.classList.add('dark');
                }
              })();
            `,
          }}
        />
      </head>
      <body className="bg-zinc-950 dark:bg-zinc-950 bg-white text-zinc-100 dark:text-zinc-100 text-zinc-900 antialiased">
        {children}
      </body>
    </html>
  );
}
