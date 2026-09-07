import type { Metadata } from 'next';
import { Inter, JetBrains_Mono } from 'next/font/google';
import './globals.css';

const inter = Inter({
  subsets: ['latin'],
  variable: '--font-sans',
  display: 'swap',
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ['latin'],
  variable: '--font-mono',
  display: 'swap',
});

export const metadata: Metadata = {
  title: 'VECTOR | Algorithmic Crypto Trading Engine',
  description: 'Real-time Algorithmic Trading Engine & Monitoring Console for Binance Perpetual Futures',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`dark ${inter.variable} ${jetbrainsMono.variable}`}>
      <body className="bg-dark-950 text-slate-100 min-h-screen antialiased selection:bg-trade-accent selection:text-white font-sans">
        {children}
      </body>
    </html>
  );
}
