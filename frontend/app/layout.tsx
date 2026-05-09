import './globals.css';
import type { Metadata, Viewport } from 'next';
import { TopBar } from '@/components/layout/TopBar';
import { PageTransition } from '@/components/layout/PageTransition';

export const metadata: Metadata = {
  title: '小本本 · XHS Knowledge Base',
  description: '把小红书收藏夹变成你的私人旅行 / 美食 / 生活知识库',
  icons: {
    icon: 'data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 24 24%22><text y=%2220%22 font-size=%2222%22>📓</text></svg>',
  },
  manifest: '/manifest.webmanifest',
};

export const viewport: Viewport = {
  themeColor: '#fafafa',
  width: 'device-width',
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="bg-mesh min-h-screen antialiased text-ink-900">
        <TopBar />
        <main className="max-w-7xl mx-auto px-4 sm:px-6 pt-6 pb-24">
          <PageTransition>{children}</PageTransition>
        </main>
      </body>
    </html>
  );
}
