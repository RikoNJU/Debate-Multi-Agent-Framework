import { Sparkles } from 'lucide-react';
import { Link } from 'react-router-dom';

export default function Navbar() {
  return (
    <nav className="sticky top-0 z-20 border-b border-white/60 bg-white/80 backdrop-blur">
      <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-4 sm:px-6 lg:px-8">
        <Link to="/" className="flex items-center gap-3 text-lg font-semibold text-slate-800">
          <span className="flex h-10 w-10 items-center justify-center rounded-full bg-gradient-to-br from-blue-600 to-indigo-600 text-white shadow-lg">
            <Sparkles size={18} />
          </span>
          <span>睿文智评</span>
        </Link>
        <div className="hidden items-center gap-6 text-sm font-medium text-slate-600 md:flex">
          <a href="#review" className="transition hover:text-blue-600">开始评审</a>
          <a href="#features" className="transition hover:text-blue-600">核心能力</a>
          <a href="#result" className="transition hover:text-blue-600">结果展示</a>
        </div>
      </div>
    </nav>
  );
}